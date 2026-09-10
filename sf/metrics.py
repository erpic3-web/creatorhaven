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
# model each video's view-decay curve, ANCHOR it to that video's known total at its known age,
# and read off the window slice — then sum across the catalogue. The outlier score sets how
# front-loaded the curve is: a banger sustains a ~1-month spike then a slowly-decaying tail; a
# flop spikes for a day then sits flat/evergreen. Calibrated against two real Studio curves
# ("Arlo went MISSING" 2.9M / 307d, and a 25k / 115d flop) — see scratchpad/residual_fit*.py.
# Shape: s(t) = (t+t0)^-p1 for t<=tb, then continues ∝ (t+t0)^-p2 (the evergreen tail).
_RES_P1 = 1.5                       # head slope (shared across all videos)
_RES_T0 = 0.5
_RES_O_LO, _RES_O_HI = 0.35, 19.0  # the two calibration outlier scores (flop / banger)
_RES_TB = (3.0, 30.0)              # spike duration in days at O_LO / O_HI
_RES_P2 = (0.3, 1.0)              # evergreen-tail slope at O_LO / O_HI
# A channel outlier this big means videos reached new audiences, which rediscover the whole back
# catalogue — the user's rule: add at least +10k views / 48h to residual (older) uploads.
RES_BOOST_OUTLIER = 8.0
RES_BOOST_PER_48H = 10000.0
# The back-catalogue integral uses the RECENT median as a typical un-sampled upload's size, which
# overstates genuinely OLD uploads (a channel had fewer viewers back then) — and the further back
# the fetched sample already reaches, the older the un-sampled remainder, so the more it overstates.
# Damping therefore falls linearly with the oldest sampled age. Calibrated to two real channels'
# Studio 28-day figures: Erik1515 (slow uploader, oldest ~312d → needs 0.31) and pixiiuwu (fast,
# oldest ~70d → needs 0.85). Refine as more real Studio numbers arrive.
RES_BACK_DAMP0 = 1.006
RES_BACK_DAMP_SLOPE = 0.002236     # per day of oldest-sampled age


def _back_damp(oldest_days):
    return max(0.25, min(1.0, RES_BACK_DAMP0 - RES_BACK_DAMP_SLOPE * (oldest_days or 0.0)))


def _decay_params(outlier):
    o = max(0.05, float(outlier or 1.0))
    x = (math.log10(o) - math.log10(_RES_O_LO)) / (math.log10(_RES_O_HI) - math.log10(_RES_O_LO))
    tb = min(52.0, max(0.5, _RES_TB[0] + (_RES_TB[1] - _RES_TB[0]) * x))
    p2 = min(1.6, max(0.0, _RES_P2[0] + (_RES_P2[1] - _RES_P2[0]) * x))
    return _RES_P1, p2, tb, _RES_T0


def _int_pow(a, b, p, t0):
    """Closed form of the integral of (t+t0)^-p from a to b (handles p==1 and p==0)."""
    a0, b0 = a + t0, b + t0
    if abs(p - 1.0) < 1e-9:
        return math.log(b0 / a0)
    return (b0 ** (1 - p) - a0 ** (1 - p)) / (1 - p)


def _decay_cum(T, p1, p2, tb, t0):
    """Cumulative area under the two-segment decay shape from 0 to T."""
    if T <= 0:
        return 0.0
    if T <= tb:
        return _int_pow(0.0, T, p1, t0)
    head = _int_pow(0.0, tb, p1, t0)
    c = (tb + t0) ** (p2 - p1)          # continuity multiplier for the tail segment
    return head + c * _int_pow(tb, T, p2, t0)


def video_window_views(total_views, age_days, window_days, outlier=1.0):
    """Estimated views a single video accumulated in its last `window_days`, from its current
    total and age. A video younger than the window returns its whole total (all views are recent)."""
    total_views = total_views or 0
    if total_views <= 0 or age_days is None or age_days <= 0 or not window_days:
        return 0.0
    if age_days <= window_days:
        return float(total_views)
    p1, p2, tb, t0 = _decay_params(outlier)
    st = _decay_cum(age_days, p1, p2, tb, t0)
    if st <= 0:
        return 0.0
    sw = _decay_cum(age_days - window_days, p1, p2, tb, t0)
    return float(total_views) * (st - sw) / st


def channel_window_views(videos, days, now=None, outlier_by_id=None, total_videos=None,
                         lifetime_views=None, channel_age_days=None):
    """Estimate a channel's total views in the last `days`.

    Core = the sum of each fetched public video's modelled window slice — anchored to that
    video's real current total, so a recent breakout's own pull AND the rediscovery bump it
    gives the rest of the catalogue are already reflected in the current view counts. When we
    have the channel's real upload count and lifetime total, a small deep-evergreen term is
    added for the OLDER uploads beyond the fetched sample (scaled by the lifetime views the
    sample doesn't represent, at the un-sampled catalogue's mid-age). Returns the parts.
    outlier_by_id: {video_id: score}; computed from the sample median if absent."""
    now = now or datetime.now(timezone.utc)
    pub = [v for v in videos if v.get("privacy") == "public" and (v.get("views") or 0) > 0]
    if not pub or not days:
        return {"views": 0, "from_uploads": 0, "back_catalog": 0, "boost": 0, "max_outlier": None, "sample": len(pub)}
    long_views_all = [v.get("views") or 0 for v in pub if not int(v.get("is_short") or 0)]
    all_views = [v.get("views") or 0 for v in pub]
    med = median(long_views_all) if long_views_all else (median(all_views) or 1)
    med_all = median(all_views) if all_views else med    # typical upload size, robust to viral skew
    if outlier_by_id is None:
        outlier_by_id = {v["video_id"]: ((v.get("views") or 0) / med if med else 1.0) for v in pub}
    total = omax = sample_views = 0.0
    ages = []
    for v in pub:
        age = days_since(v.get("published_at"), now)
        if age:
            ages.append(age)
        sample_views += v.get("views") or 0
        o = outlier_by_id.get(v["video_id"]) or 1.0
        omax = max(omax, o)
        total += video_window_views(v.get("views") or 0, age, days, o)
    # Back catalogue: uploads older than the fetched sample. Their combined views are (lifetime
    # total − what the sample already accounts for); they sit deep in the evergreen tail, so a
    # small fraction of those views lands in the window. Only meaningful when the sample doesn't
    # already cover the whole catalogue.
    back = 0.0
    if total_videos and total_videos > len(pub) and med_all > 0:
        n_missing = total_videos - len(pub)
        oldest = max(ages) if ages else float(days)
        far = channel_age_days if (channel_age_days and channel_age_days > oldest + 1) else oldest * 5.0
        far = max(far, oldest + 1.0)
        # A typical un-sampled upload (~ the channel's median views, robust to a few virals), averaged
        # over the age span it could sit in [oldest sampled .. channel age]. Integrating the window
        # fraction over that span makes the estimate rise correctly with the window size instead of
        # collapsing to a flat cap — vital for fast-uploading channels whose sample spans only weeks.
        K = 12
        mean_frac = sum(video_window_views(1.0, oldest + (far - oldest) * i / (K - 1), days, 1.0)
                        for i in range(K)) / K
        back = med_all * n_missing * mean_frac * _back_damp(oldest)
        if lifetime_views:                              # can't have gained more than the views that exist
            back = min(back, max(0.0, float(lifetime_views) - sample_views))
        total += back
    return {"views": round(total), "from_uploads": round(total - back), "back_catalog": round(back),
            "boost": 0, "max_outlier": round(omax, 2), "sample": len(pub)}


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
