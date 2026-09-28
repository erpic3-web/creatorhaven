"""Pure metric math: outlier scores, latest-content ranking, combined statistics,
the scheduled-upload calendar rule, and small formatting helpers. No I/O here so
the selftest can pin the behaviour.
"""
import math
from datetime import datetime, timezone, timedelta
from statistics import median


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def days_since(iso, now=None):
    d = parse_iso(iso)
    if not d:
        return None
    now = now or datetime.now(timezone.utc)
    return max((now - d).total_seconds() / 86400, 0.0)


def compact(n):
    if n is None:
        return "–"
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            v = math.floor(abs(n) / div * 10 + 0.5) / 10 * (1 if n >= 0 else -1)   # half-up, like JS toFixed
            s = f"{v:.1f}".rstrip("0").rstrip(".")
            return f"{s}{unit}"
    return f"{int(n):,}" if n == int(n) else f"{n:,.1f}"


def _fmt(v):
    return v if v is not None else 0


# ---------------------------------------------------------------- outliers
def outlier_scores(videos, metric="views", window=30, min_baseline=5):
    """Score = video metric / median of the previous `window` videos of the SAME
    format (short vs long). A 3.0 means 3x the channel's typical; that is the
    signal an outlier hunter wants. Videos without enough history score None."""
    vids = [v for v in videos if v.get(metric) is not None and v.get("privacy") == "public"]
    vids.sort(key=lambda v: v.get("published_at") or "", reverse=True)
    out = {}
    for fmt in (0, 1):
        same = [v for v in vids if int(v.get("is_short") or 0) == fmt]
        for i, v in enumerate(same):
            baseline = [x[metric] for x in same[i + 1:i + 1 + window] if x.get(metric)]
            if len(baseline) < min_baseline:
                out[v["video_id"]] = None
                continue
            med = median(baseline)
            out[v["video_id"]] = round(v[metric] / med, 2) if med else None
    return out


def latest_ranking(videos, n=10, metric="views", now=None):
    """Rank the newest n public uploads against the channel's typical VELOCITY
    (views per day since publish), computed against the 30 older uploads of
    the same format. Returns dicts with velocity, typical, ratio and an arrow."""
    now = now or datetime.now(timezone.utc)
    pub = [v for v in videos if v.get("privacy") == "public" and v.get(metric) is not None]
    pub.sort(key=lambda v: v.get("published_at") or "", reverse=True)

    def velocity(v):
        # Views per day over the FIRST WEEK at most: lifetime velocity decays with age, so an
        # uncapped comparison flatters every fresh upload. Exact same-age comparison needs
        # per-video daily analytics (todo when the analytics scope is present).
        age = days_since(v.get("published_at"), now)
        if age is None:
            return None
        return v[metric] / max(min(age, 7.0), 0.5)

    latest = pub[:n]
    out = []
    for v in latest:
        fmt = int(v.get("is_short") or 0)
        older = [x for x in pub[n:] if int(x.get("is_short") or 0) == fmt][:30]
        typ = [velocity(x) for x in older]
        typ = [t for t in typ if t]
        vel = velocity(v)
        ratio = None
        if vel is not None and len(typ) >= 3:
            m = median(typ)
            ratio = round(vel / m, 2) if m else None
        arrow = "→"
        if ratio is not None:
            arrow = "↑" if ratio >= 1.25 else ("↓" if ratio <= 0.75 else "→")
        out.append({"video_id": v["video_id"], "title": v.get("title"), "published_at": v.get("published_at"),
                    "is_short": fmt, "views": v.get("views"), "velocity": round(vel, 1) if vel is not None else None,
                    "ratio": ratio, "arrow": arrow, "thumb": v.get("thumb")})
    out.sort(key=lambda r: (r["ratio"] is None, -(r["ratio"] or 0)))
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


# ---------------------------------------------------------------- combined
def combine_daily(rows_by_channel):
    """Sum per-day rows across channels. rows_by_channel: {channel_id: [daily rows]}"""
    days = {}
    for rows in rows_by_channel.values():
        for r in rows:
            d = days.setdefault(r["day"], {"day": r["day"], "views": 0, "engaged_views": 0, "minutes": 0,
                                           "subs_gained": 0, "subs_lost": 0, "likes": 0, "comments": 0,
                                           "shares": 0, "revenue": 0.0, "channels": 0})
            for k in ("views", "engaged_views", "minutes", "subs_gained", "subs_lost", "likes",
                      "comments", "shares", "revenue"):
                d[k] += _fmt(r.get(k))
            d["channels"] += 1
    return [days[k] for k in sorted(days)]


def _rev_band(views, rpm):
    return {"views": int(views), "low": round(views / 1000.0 * rpm[0], 2), "high": round(views / 1000.0 * rpm[1], 2)}


def revenue_from_split(long_views, short_views, rpm_long=(2.0, 6.0), rpm_short=(0.20, 0.40)):
    long_, short_ = _rev_band(long_views, rpm_long), _rev_band(short_views, rpm_short)
    return {"long": long_, "short": short_,
            "combined": {"low": round(long_["low"] + short_["low"], 2), "high": round(long_["high"] + short_["high"], 2)},
            "rpm_long": list(rpm_long), "rpm_short": list(rpm_short)}


# Movie / compilation channels run long (feature-length) uploads that carry far more
# mid-roll ad breaks, so their effective RPM is much higher than a normal longform video.
MOVIE_RPM_LONG = (10.0, 20.0)
MOVIE_MIN_S = 3600   # "always over an hour" = a movie/compilation channel


def is_movie_channel(videos, threshold_s=MOVIE_MIN_S, min_long=4):
    """True when the channel's typical longform upload is over an hour — treat it as a
    movie/compilation channel (higher RPM). Judged on the MEDIAN longform duration so a
    few shorts-length outliers don't sway it."""
    longs = [v.get("duration_s") or 0 for v in videos
             if v.get("privacy") == "public" and not int(v.get("is_short") or 0) and v.get("duration_s")]
    if len(longs) < min_long:
        return False
    return median(longs) >= threshold_s


def estimated_revenue(videos, rpm_long=(2.0, 6.0), rpm_short=(0.20, 0.40), auto_movie=True):
    """Estimate income from public view counts when real revenue is unavailable.
    Longform: $2-6 per 1000 views (or $10-20 for movie/compilation channels, auto-detected).
    Shorts: $0.20-0.40 per 1000 views. Returns a low/high range for longform, shorts, combined."""
    if auto_movie and tuple(rpm_long) == (2.0, 6.0) and is_movie_channel(videos):
        rpm_long = MOVIE_RPM_LONG
    lv = sv = 0
    for v in videos:
        views = v.get("views") or 0
        if int(v.get("is_short") or 0):
            sv += views
        else:
            lv += views
    return revenue_from_split(lv, sv, rpm_long, rpm_short)


# ---------------------------------------------------------------- residual / evergreen views
# Public data gives a video's CURRENT total views and its age, never its daily history. To
# estimate how many views a channel got in the last N days (what Studio's "28 days" shows) we
# model each upload's view-decay curve, ANCHOR it to that upload's known total at its known
# age, read off the window slice — and sum across the WHOLE catalogue (every public upload,
# not a recent sample: on a ten-year channel the old uploads carried 45% of last year's views).
#
# The shape is a daily-rate curve r(t), t = days since publish, tabulated once at import, ONE
# PER FORMAT (long-form / Shorts): day-0 spike + launch exp + "suggested" plateau with a soft
# cut-off + evergreen power-law tail. Three observable modifiers scale the mixture weights:
#   • the upload's OUTLIER score (views / the channel's median for that format) raises the
#     evergreen weight (YouTube keeps serving its big winners: VMT's 3-year-old Shorts),
#   • the channel's views-per-subscriber (median views / subs) raises the plateau weight
#     (discovery-driven channels have long legs, subscriber-driven ones are front-loaded),
#   • the channel's upload cadence lowers the evergreen weight (frequent uploaders displace
#     their own back catalogue).
# CALIBRATED 2026-09-16 on four Studio exports (Erik1515, Kryptic, VMT, pixiiuwu: ~2,000
# public uploads with last-year truth, 20 daily curves, 16 window totals; tools/
# window_calibration.py scores any export). Blind whole-catalogue estimates land within
# ~10% on average; the 28-day window can still miss by 15-40% because a channel's CURRENT
# state (a slump, a recirculation wave on old Shorts) is not a function of its uploads' ages.
# That is what the daily snapshots below measure: a window the snapshot history covers is
# exact, and per-upload snapshots anchor every video to its own measured rate before that.
SHAPES = {
    0: {"f0": 0.0550, "wa": 0.1807, "ta": 2.0, "wb": 0.7963, "tb": 30.0, "tcut": 157.4, "s": 5.24,
        "we": 0.6079, "t0": 84.0, "q": 2.097, "gamma": 0.80},          # long-form
    1: {"f0": 0.0249, "wa": 0.1080, "ta": 9.09, "wb": 0.0655, "tb": 1980.0, "tcut": 136.7, "s": 5.0,
        "we": 0.1253, "t0": 735.3, "q": 1.883, "gamma": 0.281},        # Shorts
}
ALPHA = 0.80                   # plateau weight ~ exp(ALPHA * (log views-per-sub - LOG_VPS_REF))
BETA = 0.80                    # evergreen weight ~ (CAD_REF / uploads-per-week) ** BETA
LOG_VPS_REF = math.log(0.3)
CAD_REF = 1.5
OUTLIER_CLIP = (0.05, 300.0)
CHANNEL_MULT_RANGE = (0.2, 5.0)  # sanity clamp on the two channel-level multipliers
SHAPE_MAX_DAYS = 7400          # ~20 years; older uploads use the table's end
LAUNCH_DAYS = 60               # a video's first 60 days = "launch" views, the rest = "tail"
TAIL_K_RANGE = (0.02, 20.0)    # measured/modelled tail ratio: wide on purpose (Chica's old viral Shorts run at 0.15x the model)
PARTIAL_MIN_DAYS = 3           # fewer snapshot days than this: the public counter's lumps swamp the rate
COVER_SLACK_DAYS = 0.5         # a window short of the snapshot history by less than this still counts as measured


def _build_components(p, max_days=SHAPE_MAX_DAYS):
    """Cumulative arrays (index t+1 = share by the END of day t) for the three weighted parts:
    core (spike + launch), plateau, evergreen — kept apart so per-upload multipliers can scale them."""
    launch = [math.exp(-t / p["ta"]) for t in range(max_days + 1)]
    plat = [math.exp(-t / p["tb"]) / (1 + math.exp(min(60.0, max(-60.0, (t - p["tcut"]) / p["s"])))) for t in range(max_days + 1)]
    ever = [(1 + t / p["t0"]) ** -p["q"] for t in range(max_days + 1)]
    ls, ps, es = sum(launch), sum(plat), sum(ever)
    core, cpl, cev = [0.0], [0.0], [0.0]
    a = b = c = 0.0
    for t in range(max_days + 1):
        a += (p["f0"] if t == 0 else 0.0) + p["wa"] * launch[t] / ls
        b += p["wb"] * plat[t] / ps
        c += p["we"] * ever[t] / es
        core.append(a); cpl.append(b); cev.append(c)
    return core, cpl, cev


_COMPONENTS = {fmt: _build_components(p) for fmt, p in SHAPES.items()}

# DORMANT CHANNELS: no public upload for DORMANT_AFTER_DAYS. The four calibration channels were all
# active, and the cadence feature extrapolated "uploads rarely -> old videos stay alive" to its 5x
# clamp for a channel that stopped. On K9 (1.59M subs, last upload 635 days before its Studio
# export of 2026-08-31..09-27: 740,815 views) the active shape said 1,077,291 and was wrong in BOTH
# directions: its old Shorts got 303K (model 928K) and its old long-form 430K (model 149K). Fitted
# on K9's 251 per-video 28-day views: long-form evergreen fades ~5x slower (t0 84 -> 400 days) and
# neither format gets the cadence boost; Shorts also lose the outlier boost (its 11M-view Short got
# 6K). Result on K9: 683K modelled for the 251 public uploads (-8% vs Studio, was +45%).
# One dormant channel of evidence — per-upload snapshots correct it further after 7 days.
DORMANT_AFTER_DAYS = 180
DORMANT = {0: {"t0": 400.0, "me": 1.0, "gamma": 0.80},
           1: {"me": 0.80, "gamma": 0.0}}
_COMPONENTS_DORMANT = {0: _build_components({**SHAPES[0], "t0": DORMANT[0]["t0"]}), 1: _COMPONENTS[1]}


def _interp(arr, t):
    if t <= 0:
        return 0.0
    if t >= SHAPE_MAX_DAYS:
        return arr[-1]
    i = int(t)
    return arr[i] + (arr[i + 1] - arr[i]) * (t - i)


def shape_cum(t, is_short=0, mp=1.0, me=1.0, dormant=False):
    """Share (0..1) of an upload's eventual views that have arrived t days after publish, for
    a format with plateau multiplier mp and evergreen multiplier me (dormant = the channel
    stopped uploading: the slower-fading long-form tail)."""
    core, cpl, cev = (_COMPONENTS_DORMANT if dormant else _COMPONENTS)[1 if is_short else 0]
    tot = core[-1] + mp * cpl[-1] + me * cev[-1]
    if t <= 0 or tot <= 0:
        return 0.0
    return (_interp(core, t) + mp * _interp(cpl, t) + me * _interp(cev, t)) / tot


def catalog_context(videos, channel=None, now=None):
    """Channel-level inputs of the shape, from the catalogue itself: per-format median views
    (for outlier scores), the plateau multiplier per format (views per subscriber) and the
    evergreen multiplier (upload cadence over the last 91 days)."""
    now = now or datetime.now(timezone.utc)
    pub = [v for v in videos if v.get("privacy", "public") in (None, "public") and (v.get("views") or 0) > 0]
    allv = [v.get("views") or 0 for v in pub]
    med = {}
    for fmt in (0, 1):
        same = [v.get("views") or 0 for v in pub if int(v.get("is_short") or 0) == fmt]
        med[fmt] = median(same) if len(same) >= 3 else (median(allv) if allv else 1.0)
        med[fmt] = med[fmt] or 1.0
    subs = (channel or {}).get("subscribers") or 0
    mp = {}
    for fmt in (0, 1):
        if subs > 0:
            m = math.exp(ALPHA * (math.log(max(1e-6, med[fmt] / subs)) - LOG_VPS_REF))
            mp[fmt] = max(CHANNEL_MULT_RANGE[0], min(CHANNEL_MULT_RANGE[1], m))
        else:
            mp[fmt] = 1.0
    ages = [a for a in (days_since(v.get("published_at"), now) for v in pub) if a is not None]
    recent = sum(1 for a in ages if a <= 91)
    cadence = max(0.1, recent / 13.0)
    me_ch = max(CHANNEL_MULT_RANGE[0], min(CHANNEL_MULT_RANGE[1], (CAD_REF / cadence) ** BETA))
    last = min(ages) if ages else None
    return {"median": med, "mp": mp, "me_ch": me_ch, "cadence": round(cadence, 2), "subs": subs,
            "days_since_upload": round(last, 1) if last is not None else None,
            "dormant": bool(last is not None and last >= DORMANT_AFTER_DAYS)}


def _video_mults(views, is_short, ctx):
    fmt = 1 if is_short else 0
    if not ctx:
        return 1.0, 1.0
    o = (views or 0) / (ctx["median"].get(fmt) or 1.0)
    o = max(OUTLIER_CLIP[0], min(OUTLIER_CLIP[1], o))
    if ctx.get("dormant"):
        d = DORMANT[fmt]
        return ctx["mp"].get(fmt, 1.0), d["me"] * o ** d["gamma"]
    return ctx["mp"].get(fmt, 1.0), ctx["me_ch"] * o ** SHAPES[fmt]["gamma"]


def video_window_views(total_views, age_days, window_days, outlier=1.0, is_short=0, ctx=None):
    """Estimated views a single video accumulated in its last `window_days`, from its current
    total and age. A video younger than the window returns its whole total (all views are recent).
    `outlier` is only used when no catalogue context is given."""
    total_views = total_views or 0
    if total_views <= 0 or age_days is None or age_days <= 0 or not window_days:
        return 0.0
    if age_days <= window_days:
        return float(total_views)
    if ctx:
        mp, me = _video_mults(total_views, is_short, ctx)
    else:
        o = max(OUTLIER_CLIP[0], min(OUTLIER_CLIP[1], float(outlier or 1.0)))
        mp, me = 1.0, o ** SHAPES[1 if is_short else 0]["gamma"]
    dm = bool(ctx and ctx.get("dormant"))
    ca = shape_cum(age_days, is_short, mp, me, dm)
    if ca <= 0:
        return 0.0
    return float(total_views) * (ca - shape_cum(age_days - window_days, is_short, mp, me, dm)) / ca


def video_daily_slices(total_views, age_days, days, outlier=1.0, is_short=0, ctx=None):
    """Per day-ago slices (index 0 = the most recent day) of a video's modelled views, split
    into (launch, tail) = inside / after its first LAUNCH_DAYS days. Length `days`."""
    launch, tail = [0.0] * days, [0.0] * days
    if not total_views or not age_days or age_days <= 0:
        return launch, tail
    if ctx:
        mp, me = _video_mults(total_views, is_short, ctx)
    else:
        o = max(OUTLIER_CLIP[0], min(OUTLIER_CLIP[1], float(outlier or 1.0)))
        mp, me = 1.0, o ** SHAPES[1 if is_short else 0]["gamma"]
    dm = bool(ctx and ctx.get("dormant"))
    ca = shape_cum(age_days, is_short, mp, me, dm)
    if ca <= 0:
        return launch, tail
    scale = float(total_views) / ca
    c_m = shape_cum(LAUNCH_DAYS, is_short, mp, me, dm)
    for d in range(days):
        hi = age_days - d
        if hi <= 0:
            break
        lo = max(0.0, hi - 1.0)
        c_lo, c_hi = shape_cum(lo, is_short, mp, me, dm), shape_cum(hi, is_short, mp, me, dm)
        if hi <= LAUNCH_DAYS:
            launch[d] = (c_hi - c_lo) * scale
        elif lo >= LAUNCH_DAYS:
            tail[d] = (c_hi - c_lo) * scale
        else:
            launch[d] = (c_m - c_lo) * scale
            tail[d] = (c_hi - c_m) * scale
    return launch, tail


def channel_daily_profile(videos, days, now=None, ctx=None):
    """Channel-wide modelled views per day-ago over the last `days`: (launch[], tail[]) summed
    over every public upload with views (plus the count of uploads used and their view sum)."""
    now = now or datetime.now(timezone.utc)
    ctx = ctx or catalog_context(videos, None, now)
    launch, tail = [0.0] * days, [0.0] * days
    n = 0
    sample_views = 0
    for v in videos:
        if v.get("privacy", "public") not in (None, "public") or not (v.get("views") or 0):
            continue
        age = days_since(v.get("published_at"), now)
        if not age:
            continue
        n += 1
        sample_views += v.get("views") or 0
        l, t = video_daily_slices(v.get("views") or 0, age, days, is_short=int(v.get("is_short") or 0), ctx=ctx)
        for d in range(days):
            launch[d] += l[d]
            tail[d] += t[d]
    return launch, tail, n, sample_views


def back_catalog_views(videos, days, total_videos, lifetime_views, channel_age_days, now=None, ctx=None):
    """Window views of the uploads OLDER than the fetched catalogue (only when the channel has
    more uploads than we fetched): a typical old upload = the median of the oldest quarter we
    did fetch, at ages spread between the oldest fetched upload and the channel's birth."""
    now = now or datetime.now(timezone.utc)
    pub = [v for v in videos if v.get("privacy", "public") in (None, "public") and (v.get("views") or 0) > 0]
    if not total_videos or total_videos <= len(pub) or not pub or not days:
        return 0.0
    ages = sorted(((days_since(v.get("published_at"), now) or 0, v.get("views") or 0) for v in pub), reverse=True)
    oldest = ages[0][0] if ages else float(days)
    old_quarter = [vw for _, vw in ages[:max(3, len(ages) // 4)]]
    typical = median(old_quarter) if old_quarter else 0
    n_missing = total_videos - len(pub)
    far = channel_age_days if (channel_age_days and channel_age_days > oldest + 1) else oldest * 3.0
    far = max(far, oldest + 1.0)
    K = 12
    mean_frac = sum(video_window_views(typical, oldest + (far - oldest) * i / (K - 1), days, ctx=ctx) / typical
                    for i in range(K)) / K if typical else 0.0
    back = typical * n_missing * mean_frac
    if lifetime_views:
        sample_views = sum(v.get("views") or 0 for v in pub)
        back = min(back, max(0.0, float(lifetime_views) - sample_views))
    return back


def tail_memory(window_days):
    """How far back the measured tail level keeps correcting the model, in days (inf = the whole
    window). Scored on six channels (tools/window_calibration.py): 3- and 6-month windows follow
    the channel's CURRENT level best, while the 1-year window needs the model's own history back
    after about two months: a burst 6-10 months ago (Chica's viral Shorts) lives in the uploads'
    ages and view counts, not in today's rate."""
    return math.inf if window_days <= 182 else 60.0


# mean |error| (%) of a partly measured / anchored window on the calibration channels, by window
# and days of snapshots held (python tools/window_calibration.py --partial, 2026-09-28: four
# Studio exports + Chica from public counters; the pure model, 0 days, averaged 127-134% on 7/28d)
TYPICAL_ERROR = {7: ((3, 9),),
                 28: ((3, 20), (7, 14), (14, 7), (21, 6)),
                 91: ((3, 22), (7, 16), (14, 16), (21, 15), (28, 16)),
                 182: ((3, 20), (7, 18), (14, 17), (21, 17), (28, 18)),
                 365: ((3, 12), (7, 12), (14, 12), (21, 12), (28, 13))}


def typical_error(window_days, measured_days):
    table = TYPICAL_ERROR[min(TYPICAL_ERROR, key=lambda w: abs(w - window_days))]
    got = [e for s_, e in table if s_ <= measured_days]
    return got[-1] if got else None


def channel_view_windows(videos, windows, now=None, measured=None, total_videos=None,
                         lifetime_views=None, channel_age_days=None, channel=None, retention_days=None):
    """Channel-wide views over several windows. Measurement comes first:

      • the daily snapshots cover the window    -> the exact counter difference ("measured")
      • PARTIAL_MIN_DAYS+ snapshot days held    -> the measured days + the model for the rest, its
                                                   TAIL (old uploads' views) scaled by what the
                                                   measured days say (k = measured minus modelled
                                                   launches, over the modelled tail) and fading back
                                                   to the model per tail_memory(); each upload's
                                                   launch stays as modelled ("partial", or
                                                   "estimate" when the window is longer than the
                                                   snapshots may be kept)
      • fewer snapshot days                     -> the pure model ("estimate")

    `measured` = measured_from_snapshots(...). `retention_days` = how long this channel's
    snapshots may be kept (YouTube: 30 days for channels we are not authorized for; None = no
    limit): a window beyond it can never be measured, so it is an estimate for good.
    Returns {label: {...}} keyed like `windows` = [(label, days), ...]."""
    now = now or datetime.now(timezone.utc)
    horizon = max(d for _, d in windows) if windows else 0
    ctx = catalog_context(videos, channel, now)
    pub = [v for v in videos if v.get("privacy", "public") in (None, "public") and (v.get("views") or 0) > 0]
    m = measured or {}
    span = int(m.get("days") or 0)
    raw = m.get("raw_views", m.get("views"))
    per_window = m.get("per_window") or {}
    launch, tail = [0.0] * horizon, [0.0] * horizon
    n = 0
    for v in pub:
        age = days_since(v.get("published_at"), now)
        if not age:
            continue
        l, t = video_daily_slices(v.get("views") or 0, age, horizon, is_short=int(v.get("is_short") or 0), ctx=ctx)
        for d in range(horizon):
            launch[d] += l[d]
            tail[d] += t[d]
        n += 1
    k = None
    if raw is not None and PARTIAL_MIN_DAYS <= span <= horizon:
        ml, mt = sum(launch[:span]), sum(tail[:span])
        if mt > 0:
            k = max(TAIL_K_RANGE[0], min(TAIL_K_RANGE[1], (float(raw) - ml) / mt))
    out = {}
    for label, days in windows:
        back = back_catalog_views(videos, days, total_videos, lifetime_views, channel_age_days, now, ctx)
        model = sum(launch[:days]) + sum(tail[:days])
        beyond = retention_days is not None and days > retention_days
        part = {"days": days, "sample": n, "back_catalog": round(back), "model": round(model + back),
                "measured_days": span, "tail_k": round(k, 3) if k is not None else None, "beyond_retention": beyond}
        if per_window.get(days) is not None:
            gap = (m.get("gaps") or {}).get(days) or 0.0
            # a reading missing around the window's start (the site was off) = interpolated across the gap
            part.update({"views": round(per_window[days]), "method": "measured", "exact_in_days": 0,
                         "typical_error_pct": 0, "gap_days": gap, "interpolated": gap > 1.5})
        elif k is not None and span < days:
            mem = tail_memory(days)
            older = fsum = 0.0
            for d in range(span, days):
                f = 1.0 + (k - 1.0) * (1.0 if mem == math.inf else math.exp(-(d - span) / mem))
                older += launch[d] + tail[d] * f
                fsum += f
            # uploads we did not fetch are old = all tail: the same correction over the uncovered days
            # (their share of the measured days is already inside the measured delta)
            back_part = back * fsum / days
            part.update({"views": round(float(raw) + older + back_part), "method": "estimate" if beyond else "partial",
                         "exact_in_days": None if beyond else days - span,
                         "typical_error_pct": typical_error(days, span)})
        else:
            part.update({"views": round(model + back), "method": "estimate",
                         "exact_in_days": None if beyond else max(0, days - span), "typical_error_pct": None})
        part["from_uploads"] = part["views"] - part["back_catalog"]
        part["context"] = {"cadence": ctx["cadence"], "plateau_x": {"long": round(ctx["mp"][0], 2), "short": round(ctx["mp"][1], 2)},
                           "evergreen_x": None if ctx.get("dormant") else round(ctx["me_ch"], 2),
                           "dormant": bool(ctx.get("dormant")), "days_since_upload": ctx.get("days_since_upload")}
        out[label] = part
    return out


def channel_window_views(videos, days, now=None, outlier_by_id=None, total_videos=None,
                         lifetime_views=None, channel_age_days=None, measured=None, channel=None):
    """Estimate a channel's total views in the last `days` (single-window wrapper around
    channel_view_windows; `outlier_by_id` is accepted for compatibility)."""
    if not days:
        return {"views": 0, "from_uploads": 0, "back_catalog": 0, "boost": 0, "max_outlier": None, "sample": 0, "method": "model"}
    w = channel_view_windows(videos, [("w", days)], now=now, measured=measured, total_videos=total_videos,
                             lifetime_views=lifetime_views, channel_age_days=channel_age_days, channel=channel)["w"]
    w["boost"] = 0
    w["max_outlier"] = None
    return w


def _snap_time(r):
    """When a snapshot's counter was read: its fetch time when recorded, else the start of its day."""
    ts = r.get("fetched_at")
    if ts:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.strptime(r["day"], "%Y-%m-%d")


def _interp_at(pts, target):
    """Linear interpolation of a (datetime, value) series at `target`."""
    before = max((p for p in pts if p[0] <= target), key=lambda p: p[0])
    after = min((p for p in pts if p[0] >= target), key=lambda p: p[0])
    if after[0] == before[0]:
        return before[1]
    frac = (target - before[0]).total_seconds() / (after[0] - before[0]).total_seconds()
    return before[1] + (after[1] - before[1]) * frac


def measured_from_snapshots(rows, windows):
    """Daily channel-total snapshots [{day, views, fetched_at?}...] -> the `measured` argument of
    channel_view_windows: the counter's change over the whole history we hold (raw_views, over
    `days`), a trimmed per-day rate, and the EXACT change for every window the history covers
    (interpolated between the two neighbouring readings, at the moments they were read).
    None when fewer than two readings exist."""
    pts = []
    for r in rows or []:
        if r.get("views") is None or not r.get("day"):
            continue
        try:
            pts.append((_snap_time(r), int(r["views"])))
        except Exception:
            continue
    pts.sort()
    if len(pts) < 2:
        return None
    first_t, first_v = pts[0]
    latest_t, latest_views = pts[-1]
    span_f = (latest_t - first_t).total_seconds() / 86400
    if span_f <= 0:
        return None
    raw = max(0, latest_views - first_v)
    per_window, gaps = {}, {}
    for _, days in windows:
        target = latest_t - timedelta(days=days)
        if target >= first_t:
            per_window[days] = max(0.0, latest_views - _interp_at(pts, target))
            before = max(p[0] for p in pts if p[0] <= target)
            after = min(p[0] for p in pts if p[0] >= target)
            gaps[days] = round((after - before).total_seconds() / 86400, 1)   # days between the readings around the window's start
        elif (first_t - target).total_seconds() / 86400 <= COVER_SLACK_DAYS:
            per_window[days] = max(0.0, raw / span_f * days)      # a few hours short: the measured rate
            gaps[days] = 0.0
    # The channel total updates in lumps (a 300K jump one day, a crawl the next), so the RATE is
    # trimmed: with 3+ intervals, one whose per-day rate is over 3x the median of the others is
    # dropped and the span re-scaled. (The window deltas above stay exact.)
    views, trimmed = raw, False
    ivs = [((pts[i][0] - pts[i - 1][0]).total_seconds() / 86400, pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts))]
    ivs = [(d, v) for d, v in ivs if d > 0]
    if len(ivs) >= 3:
        rates = [v / d for d, v in ivs]
        hi = max(range(len(ivs)), key=lambda i: rates[i])
        others = [rates[i] for i in range(len(ivs)) if i != hi]
        med = sorted(others)[len(others) // 2]
        if med >= 0 and rates[hi] > 3 * max(med, 1e-9):
            rest_days = sum(d for i, (d, v) in enumerate(ivs) if i != hi)
            rest_views = sum(v for i, (d, v) in enumerate(ivs) if i != hi)
            if rest_days > 0:
                views, trimmed = max(0.0, rest_views / rest_days * span_f), True
    return {"days": int(round(span_f)), "span_days": round(span_f, 2), "views": views, "raw_views": raw,
            "trimmed": trimmed, "rate": views / span_f, "since": first_t.strftime("%Y-%m-%d"), "per_window": per_window,
            "gaps": gaps}


def measured_videos_from_snapshots(rows, latest_day=None, max_days=120):
    """Per-upload deltas from daily per-video snapshots [{video_id, day, views}...]: for every
    upload seen on the latest day, its view change since its oldest snapshot within `max_days`.
    Returns {video_id: {"days": span, "views": delta, "since": day}} (uploads with one day only
    are left out)."""
    by = {}
    for r in rows or []:
        if r.get("views") is None or not r.get("day") or not r.get("video_id"):
            continue
        try:
            by.setdefault(r["video_id"], []).append((datetime.strptime(r["day"], "%Y-%m-%d"), int(r["views"])))
        except Exception:
            continue
    if not by:
        return {}
    if latest_day is None:
        latest = max(p[0] for pts in by.values() for p in pts)
    else:
        latest = datetime.strptime(latest_day, "%Y-%m-%d")
    out = {}
    for vid, pts in by.items():
        pts.sort()
        if pts[-1][0] != latest:
            continue
        old = [p for p in pts if (latest - p[0]).days <= max_days]
        if len(old) < 2:
            continue
        first = old[0]
        span = (latest - first[0]).days
        if span <= 0:
            continue
        out[vid] = {"days": span, "views": max(0, pts[-1][1] - first[1]), "since": first[0].strftime("%Y-%m-%d")}
    return out


# Assumed average retention for the public watch-hours estimate. Public data gives
# duration and view count but never real average-view-duration, so this is a stated
# assumption, not a measurement — the UI labels every watch-hours figure "est.".
EST_RETENTION = 0.5


def public_window_stats(videos, days=None, now=None, channel_stats=None, window_views=None,
                        rpm_long=(2.0, 6.0), rpm_short=(0.20, 0.40), retention=EST_RETENTION, top_n=15):
    """Everything we can derive about a channel we do NOT own, for uploads PUBLISHED
    within the last `days` (days falsy -> lifetime / whole sample).

    This is the honest public proxy: 'the views the videos posted in this window have
    accumulated', not the Analytics-API 'views the channel received during this window'
    (that one needs OAuth on an owned channel or the extension on a delegated one). For
    lifetime, channel_stats.views/videos are the authoritative totals; the per-video
    sums (likes, comments, watch hours) still come from the newest scanned uploads.
    """
    now = now or datetime.now(timezone.utc)
    pub = [v for v in videos if v.get("privacy") == "public"]
    pub.sort(key=lambda v: v.get("published_at") or "", reverse=True)
    lifetime = not days
    if lifetime:
        win = pub
    else:
        cutoff = now - timedelta(days=days)
        win = [v for v in pub if (parse_iso(v.get("published_at")) or now) >= cutoff]

    longs = [v for v in win if not int(v.get("is_short") or 0)]
    shorts = [v for v in win if int(v.get("is_short") or 0)]
    vv = [v.get("views") or 0 for v in win]
    sample_views = sum(vv)
    long_views = sum(v.get("views") or 0 for v in longs)
    short_views = sum(v.get("views") or 0 for v in shorts)
    likes = sum(v.get("likes") or 0 for v in win)
    comments = sum(v.get("comments") or 0 for v in win)
    watch_s = sum((v.get("views") or 0) * (v.get("duration_s") or 0) * retention for v in win)

    dates = sorted(d for d in (parse_iso(v.get("published_at")) for v in win) if d)
    span_days = (now - dates[0]).total_seconds() / 86400 if dates else None
    if days and span_days:
        pace_days = min(float(days), max(span_days, 1e-6))
    else:
        pace_days = span_days or (float(days) if days else None)

    stats = channel_stats or {}
    movie = is_movie_channel(videos)
    rpm_long_eff = MOVIE_RPM_LONG if (movie and tuple(rpm_long) == (2.0, 6.0)) else rpm_long
    # "Views in this window" — prefer the channel-wide snapshot delta (window_views): it counts
    # views gained on ALL videos in the period, so EVERGREEN/residual views on older uploads are
    # included, not just the views on videos posted this window. Fall back to the upload-sample
    # sum until enough daily snapshots exist. Lifetime uses the channel's authoritative total.
    residual = None
    if lifetime:
        total_views = stats.get("views") if stats.get("views") is not None else sample_views
        views_source = "lifetime"
    elif window_views is not None and window_views > 0:
        total_views = int(window_views)
        views_source = "channel_wide"        # measured: snapshot delta, incl. residual on old videos
    else:
        # No daily-snapshot history yet -> model the residual/evergreen decay of every upload
        # and sum the window slice (this is how the 28-day channel-wide number stays accurate
        # for a channel we've only just started tracking).
        residual = channel_window_views(videos, days, now=now, total_videos=stats.get("videos"),
                                        lifetime_views=stats.get("views"))
        if residual["views"] > 0:
            total_views = residual["views"]
            views_source = "estimated"        # decay-model estimate, incl. residual on old videos
        else:
            total_views = sample_views
            views_source = "uploads_in_window"
    # Revenue: scale the authoritative view count by the sample's long/short mix, unless we only
    # have the window's own uploads (then the split is exact from those uploads).
    if views_source in ("lifetime", "channel_wide", "estimated") and total_views and sample_views:
        long_share = long_views / sample_views
        rev = revenue_from_split(total_views * long_share, total_views * (1 - long_share), rpm_long_eff, rpm_short)
    else:
        rev = revenue_from_split(long_views, short_views, rpm_long_eff, rpm_short)

    by_views = sorted(win, key=lambda v: v.get("views") or 0, reverse=True)
    top_videos = [{"video_id": v["video_id"], "title": v.get("title"), "thumb": v.get("thumb"),
                   "views": v.get("views"), "likes": v.get("likes"), "comments": v.get("comments"),
                   "published_at": v.get("published_at"), "is_short": int(v.get("is_short") or 0)}
                  for v in by_views[:top_n]]

    sample_capped = False
    if days and pub:
        oldest = parse_iso(pub[-1].get("published_at"))
        sample_capped = bool(oldest and oldest >= (now - timedelta(days=days)))

    return {
        "window_days": int(days) if days else 0, "lifetime": lifetime,
        "uploads": len(win), "uploads_long": len(longs), "uploads_short": len(shorts),
        "views": int(total_views or 0), "sample_views": sample_views,
        "long_views": long_views, "shorts_views": short_views,
        "likes": likes, "comments": comments, "engagements": likes + comments,
        "engagement_rate": round((likes + comments) / sample_views, 4) if sample_views else None,
        "avg_views": round(sample_views / len(win)) if win else None,
        "median_views": round(median(vv)) if vv else None,
        "watch_hours": round(watch_s / 3600), "watch_hours_est": True, "retention": retention,
        "views_per_day": (round(total_views / float(days)) if (days and views_source in ("channel_wide", "estimated") and total_views)
                          else (round(sample_views / pace_days) if pace_days and pace_days > 0 else None)),
        "uploads_per_week": round(len(win) / (pace_days / 7), 2) if pace_days and pace_days >= 7 else None,
        "est_revenue": rev, "movie_channel": movie, "views_source": views_source,
        "residual": residual,
        "total_videos": stats.get("videos") if lifetime else None,
        "best": top_videos[0] if top_videos else None, "top_videos": top_videos,
        "sample_capped": sample_capped, "sample_size": len(pub),
    }


def sum_totals(rows):
    tot = {"views": 0, "engaged_views": 0, "minutes": 0, "subs_gained": 0, "subs_lost": 0,
           "likes": 0, "comments": 0, "shares": 0, "revenue": 0.0, "days": len(rows)}
    for r in rows:
        for k in tot:
            if k != "days":
                tot[k] += _fmt(r.get(k))
    tot["net_subs"] = tot["subs_gained"] - tot["subs_lost"]
    tot["engaged_ratio"] = round(tot["engaged_views"] / tot["views"], 3) if tot["views"] else None
    return tot


def combined_channel_stats(channels):
    tot = {"channels": len(channels), "subscribers": 0, "views": 0, "videos": 0}
    for c in channels:
        s = c.get("stats") or {}
        for k in ("subscribers", "views", "videos"):
            tot[k] += _fmt(s.get(k))
    return tot


# ---------------------------------------------------------------- calendar
GOOD_OVER = 7   # the schedule bot's rule: stocked when MORE than 7 videos are scheduled


def scheduled(videos, now=None):
    """Videos still private with a future publishAt = exactly Studio's Scheduled tab."""
    now = now or datetime.now(timezone.utc)
    out = []
    for v in videos:
        if v.get("privacy") != "private" or not v.get("publish_at"):
            continue
        t = parse_iso(v["publish_at"])
        if not t or t <= now:
            continue
        out.append(dict(v, publish_dt=t))
    out.sort(key=lambda v: v["publish_dt"])
    return out


def calendar(videos_by_channel, now=None, days=60):
    """Per-channel stock (count, next, ok flag) + per-day buckets for the grid."""
    now = now or datetime.now(timezone.utc)
    per_channel, per_day = [], {}
    for cid, (title, vids) in videos_by_channel.items():
        sched = scheduled(vids, now)
        per_channel.append({"channel_id": cid, "title": title, "count": len(sched),
                            "next": sched[0]["publish_at"] if sched else None, "ok": len(sched) > GOOD_OVER,
                            "missing_description": sum(1 for v in sched if not v.get("description_len"))})
        for v in sched:
            if v["publish_dt"] > now + timedelta(days=days):
                continue
            day = v["publish_dt"].astimezone().strftime("%Y-%m-%d")
            per_day.setdefault(day, []).append({"video_id": v["video_id"], "title": v.get("title"),
                                                "channel_id": cid, "channel": title,
                                                "publish_at": v["publish_at"], "thumb": v.get("thumb"),
                                                "is_short": v.get("is_short")})
    per_channel.sort(key=lambda c: (c["ok"], c["count"], c["title"] or ""))
    return {"channels": per_channel, "days": per_day, "good_over": GOOD_OVER}


def milestones_crossed(prev, cur, ladder=(1000, 10000, 50000, 100000, 250000, 500000, 1000000,
                                          2500000, 5000000, 10000000)):
    if prev is None or cur is None:
        return []
    return [m for m in ladder if prev < m <= cur]
