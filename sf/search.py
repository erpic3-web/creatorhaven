"""Channel search + lookup (the ViewStats-style search bar).

search_channels(): "@handle" / channel URL / UC-id resolve directly (1 quota unit);
free-text goes through search.list type=channel (100 units) then channels.list for
the real numbers (1 unit).  Results are cached in the store's settings table for a day
so repeated queries are free.

lookup_channel(): one channel's full card - stats, recent uploads (uploads playlist,
1 unit per 50 + 1 unit for the video stats), derived metrics (avg/median views, uploads
per week, outlier scores, shorts share, views per day of channel age) and the growth
between the daily snapshots this module records every time a channel is looked at or
synced (public channels have no Analytics API, so the snapshots ARE their history).
"""
import json
import re
import statistics
import time
from datetime import timedelta, datetime, timezone

from . import metrics

SEARCH_TTL = 24 * 3600
LOOKUP_TTL = 3600
_UC_RE = re.compile(r"(UC[0-9A-Za-z_-]{20,})")
_HANDLE_RE = re.compile(r"@([A-Za-z0-9._-]{3,})")
_URL_USER_RE = re.compile(r"youtube\.com/(?:c/|user/)([A-Za-z0-9._-]+)")


def _ensure_tables(store):
    store._exec("""CREATE TABLE IF NOT EXISTS channel_snapshots (
        channel_id TEXT NOT NULL, day TEXT NOT NULL, subscribers INTEGER, views INTEGER, videos INTEGER,
        fetched_at REAL, PRIMARY KEY (channel_id, day))""")
    store._exec("""CREATE TABLE IF NOT EXISTS video_snapshots (
        channel_id TEXT NOT NULL, video_id TEXT NOT NULL, day TEXT NOT NULL, views INTEGER,
        PRIMARY KEY (video_id, day))""")
    store._exec("CREATE INDEX IF NOT EXISTS video_snapshots_ch ON video_snapshots (channel_id, day)")


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_snapshot(store, channel_id, stats):
    """Called on every lookup and every public sync: one row per channel per UTC day."""
    if not channel_id or stats is None:
        return
    _ensure_tables(store)
    store._exec("INSERT OR REPLACE INTO channel_snapshots VALUES (?,?,?,?,?,?)",
                (channel_id, _today(), stats.get("subscribers"), stats.get("views"), stats.get("videos"), time.time()))


def record_video_snapshots(store, channel_id, uploads):
    """One row per upload per UTC day (every lookup and every sync): the per-upload view history
    that lets the window estimate anchor each video to its own measured rate."""
    if not channel_id or not uploads:
        return 0
    _ensure_tables(store)
    day = _today()
    rows = [(channel_id, v.get("video_id"), day, int(v.get("views") or 0))
            for v in uploads if v.get("video_id") and v.get("views") is not None]
    return store._many("INSERT OR REPLACE INTO video_snapshots VALUES (?,?,?,?)", rows)


def video_snapshot_deltas(store, channel_id, max_days=120):
    """{video_id: {days, views, since}} = each upload's view change since its oldest snapshot
    within `max_days` of the channel's latest snapshot day (see metrics.measured_videos_from_snapshots)."""
    _ensure_tables(store)
    latest = store._one("SELECT MAX(day) AS day FROM video_snapshots WHERE channel_id=?", (channel_id,))
    if not latest or not latest.get("day"):
        return {}
    since = (datetime.strptime(latest["day"], "%Y-%m-%d") - timedelta(days=max_days)).strftime("%Y-%m-%d")
    rows = store._all("SELECT video_id, day, views FROM video_snapshots WHERE channel_id=? AND day>=? ORDER BY day",
                      (channel_id, since))
    return metrics.measured_videos_from_snapshots(rows, latest["day"], max_days)


def snapshots(store, channel_id, days=90):
    _ensure_tables(store)
    return store._all("SELECT day, subscribers, views, videos FROM channel_snapshots WHERE channel_id=? "
                      "ORDER BY day DESC LIMIT ?", (channel_id, days))[::-1]


def _cache_get(store, key, ttl):
    raw = store.get_setting(key)
    if not raw:
        return None
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if time.time() - obj.get("ts", 0) > ttl:
        return None
    return obj.get("data")


def _cache_put(store, key, data):
    store.set_setting(key, json.dumps({"ts": time.time(), "data": data}))


def _direct_ref(q):
    """('id', UC...) | ('handle', name) | None for free text."""
    m = _UC_RE.search(q)
    if m:
        return "id", m.group(1)
    m = _HANDLE_RE.search(q)
    if m:
        return "handle", m.group(1)
    m = _URL_USER_RE.search(q)
    if m:
        return "handle", m.group(1)
    if re.fullmatch(r"[A-Za-z0-9._-]{3,30}", q) and " " not in q and q.lower() != q.lower().replace("_", ""):
        return "handle", q  # looks like a handle typed without the @
    return None


def _tracked_ids(store):
    return {c["channel_id"] for c in store.channels()}


def _slim(ch, tracked):
    return {k: ch.get(k) for k in ("channel_id", "title", "handle", "thumb", "subscribers", "hidden_subs",
                                   "views", "videos", "published_at", "country")} | {"tracked": ch.get("channel_id") in tracked}


def search_channels(store, yt, q, limit=10):
    q = (q or "").strip()
    if len(q) < 2:
        return {"query": q, "results": [], "units": 0}
    key = "search:" + q.lower()
    cached = _cache_get(store, key, SEARCH_TTL)
    tracked = _tracked_ids(store)
    if cached is not None:
        for r in cached:
            r["tracked"] = r["channel_id"] in tracked
        return {"query": q, "results": cached, "units": 0, "cached": True}
    units = 0
    results = []
    ref = _direct_ref(q)
    if ref:
        kind, val = ref
        ch = yt.channels_by_ids([val]) if kind == "id" else [yt.channel_by_handle(val)]
        ch = [c for c in ch if c]
        units += 1
        results = [_slim(c, tracked) for c in ch]
    if not results:
        hits = yt.search(q=q, type_="channel", limit=limit)
        units += 100
        ids = [h["channel_id"] for h in hits if h.get("channel_id")]
        if ids:
            chans = {c["channel_id"]: c for c in yt.channels_by_ids(ids)}
            units += 1
            results = [_slim(chans[i], tracked) for i in ids if i in chans]
            results.sort(key=lambda r: r.get("subscribers") or 0, reverse=True)
    # tracked channels that match the words are free extras (already in the store)
    ql = q.lower()
    for c in store.channels():
        st = json.loads(c.get("stats_json") or "{}")
        if ql in (c.get("title") or "").lower() or ql.lstrip("@") in (c.get("handle") or "").lower():
            if c["channel_id"] not in {r["channel_id"] for r in results}:
                results.append(_slim({**c, **st}, tracked))
    for r in results:
        record_snapshot(store, r["channel_id"], r)
    _cache_put(store, key, results)
    return {"query": q, "results": results, "units": units}


def _parse_ts(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


CATALOG_LIMIT = 1200     # uploads fetched per lookup for the window estimate (24 pages + 24 videos.list calls = 48 units max; pixiiuwu has 1,200)


def lookup_channel(store, yt, channel_id, n_videos=30, force=False):
    key = f"lookup:{channel_id}:{n_videos}"
    if not force:
        cached = _cache_get(store, key, LOOKUP_TTL)
        if cached is not None:
            cached["growth"] = growth(store, channel_id)
            cached["windows"] = _view_windows((cached.get("videos") or []) + (cached.get("catalog") or []),
                                              cached.get("channel") or {}, store, channel_id)
            cached["tracked"] = channel_id in _tracked_ids(store)
            cached["cached"] = True
            return cached
    chans = yt.channels_by_ids([channel_id])
    if not chans:
        raise ValueError("channel not found")
    ch = chans[0]
    units = 1
    videos, catalog = [], []
    if ch.get("uploads_playlist"):
        # The newest `n_videos` uploads carry the per-video table + averages; the REST of the
        # catalogue (up to CATALOG_LIMIT, ~2 quota units per 100 uploads) only feeds the window
        # estimate, where old uploads matter: on a ten-year channel they carried 45% of the
        # views of the last year, and a recent-only sample cannot see them.
        want = max(n_videos, min(CATALOG_LIMIT, int(ch.get("videos") or 0) or CATALOG_LIMIT))
        ids = yt.playlist_video_ids(ch["uploads_playlist"], limit=want)
        units += max(1, (len(ids) + 49) // 50)
        if ids:
            fetched = yt.videos(ids)
            units += max(1, (len(ids) + 49) // 50)
            fetched.sort(key=lambda v: v.get("published_at") or "", reverse=True)
            videos = fetched[:n_videos]
            catalog = [{k: v.get(k) for k in ("video_id", "published_at", "views", "is_short", "privacy", "duration_s")}
                       for v in fetched[n_videos:]]
    now = datetime.now(timezone.utc)
    for v in videos:
        ts = _parse_ts(v.get("published_at") or "")
        age_d = max(1e-3, (now - ts).total_seconds() / 86400) if ts else None
        v["age_days"] = round(age_d, 1) if age_d else None
        v["views_per_day"] = round((v.get("views") or 0) / age_d) if age_d else None
    views_list = [v.get("views") or 0 for v in videos]
    longs = [v for v in videos if not v.get("is_short")]
    long_views = [v.get("views") or 0 for v in longs] or views_list
    median = statistics.median(long_views) if long_views else 0
    for v in videos:
        v["outlier"] = round((v.get("views") or 0) / median, 2) if median else None
    dates = sorted(t for t in (_parse_ts(v.get("published_at") or "") for v in videos) if t)
    span_days = (dates[-1] - dates[0]).total_seconds() / 86400 if len(dates) > 1 else None
    created = _parse_ts(ch.get("published_at") or "")
    age_days = max(1, (now - created).days) if created else None
    metric = {
        "avg_views": round(statistics.mean(views_list)) if views_list else None,
        "median_views": round(median) if median else None,
        "avg_views_long": round(statistics.mean(long_views)) if long_views else None,
        "uploads_per_week": round(len(dates) / (span_days / 7), 2) if span_days and span_days > 0 else None,
        "shorts_share": round(100 * sum(1 for v in videos if v.get("is_short")) / len(videos)) if videos else None,
        "views_per_day_lifetime": round((ch.get("views") or 0) / age_days) if age_days else None,
        "views_per_sub": round((ch.get("views") or 0) / (ch.get("subscribers") or 1), 1) if ch.get("subscribers") else None,
        "avg_likes": round(statistics.mean([v.get("likes") or 0 for v in videos])) if videos else None,
        "avg_comments": round(statistics.mean([v.get("comments") or 0 for v in videos])) if videos else None,
        "last_upload": dates[-1].isoformat() if dates else None,
        "channel_age_days": age_days,
        "sample": len(videos),
    }
    top = sorted(videos, key=lambda v: v.get("views") or 0, reverse=True)[:5]
    record_snapshot(store, channel_id, ch)
    try:
        record_video_snapshots(store, channel_id, videos + catalog)
    except Exception:
        pass
    data = {"channel": ch, "metrics": metric, "videos": videos, "catalog": catalog, "top": [v["video_id"] for v in top],
            "units": units, "fetched_at": time.time()}
    _cache_put(store, key, data)
    data["growth"] = growth(store, channel_id)
    data["windows"] = _view_windows(videos + catalog, ch, store, channel_id)
    data["tracked"] = channel_id in _tracked_ids(store)
    return data


# period selector: channel-wide view estimates (residual decay model) over each window
_WINDOWS = (("28d", 28), ("3mo", 91), ("6mo", 182), ("1yr", 365))


def _view_windows(videos, ch, store=None, channel_id=None):
    """'Views in the last N' for a public channel: every fetched upload's modelled window slice
    (metrics.channel_view_windows), anchored to the daily channel-total snapshots we hold —
    a window the snapshots already cover is MEASURED, a partly covered one blends the measured
    days with a tail-calibrated model, and a fresh channel gets the pure model ('est')."""
    now = datetime.now(timezone.utc)
    total_videos = ch.get("videos")
    lifetime_views = ch.get("views")
    created = _parse_ts(ch.get("published_at") or "")
    channel_age_days = (now - created).days if created else None
    measured = None
    if store is not None and channel_id:
        try:
            measured = metrics.measured_from_snapshots(snapshots(store, channel_id, 800), _WINDOWS)
            per_video = video_snapshot_deltas(store, channel_id)
            if per_video:
                measured = measured or {"days": 0, "views": None, "per_window": {}}
                measured["videos"] = per_video
        except Exception:
            measured = None
    res = metrics.channel_view_windows(videos, _WINDOWS, now=now, measured=measured, total_videos=total_videos,
                                       lifetime_views=lifetime_views, channel_age_days=channel_age_days, channel=ch)
    out = {}
    for label, d in _WINDOWS:
        est = res[label]
        out[label] = {"days": d, "views": est["views"], "from_uploads": est.get("from_uploads"),
                      "back_catalog": est.get("back_catalog", 0), "model": est.get("model"),
                      "method": est.get("method"), "measured_days": est.get("measured_days"),
                      "measured_since": (measured or {}).get("since"), "tail_k": est.get("tail_k"),
                      "videos_measured": est.get("videos_measured", 0), "context": est.get("context"),
                      "max_outlier": None, "sample": est["sample"]}
    out["lifetime"] = {"days": 0, "views": ch.get("views"), "boost": 0, "method": "measured",
                       "max_outlier": None, "sample": len(videos)}
    return out


# ---------------------------------------------------------------- outlier baselines (extension)
BASELINE_TTL = 24 * 3600


def _baseline_from(cid, ch, vids):
    pub = [v for v in vids if (v.get("privacy") or "public") == "public"]
    longs = [v.get("views") or 0 for v in pub if not int(v.get("is_short") or 0)]
    shorts = [v.get("views") or 0 for v in pub if int(v.get("is_short") or 0)]
    allv = [v.get("views") or 0 for v in pub]
    return {"id": cid, "title": ch.get("title"), "handle": ch.get("handle"),
            "median_long": round(statistics.median(longs)) if longs else None,
            "median_short": round(statistics.median(shorts)) if shorts else None,
            "median": round(statistics.median(allv)) if allv else None,
            "subs": ch.get("subscribers"), "sample": len(pub)}


def baseline_cached(store, ident):
    """The badge baseline when it costs no quota (cached within 24 h, or a tracked channel), else None."""
    ident = (ident or "").strip()
    if not ident:
        return None
    c = _cache_get(store, "baseline:" + ident.lower(), BASELINE_TTL)
    if c is not None:
        return c
    m = _UC_RE.search(ident)
    if m and store.channel(m.group(1)):
        return channel_baseline(store, None, ident)
    return None


def channel_baseline(store, yt, ident, force=False):
    """Cheap per-channel outlier baseline for the extension's on-YouTube badges: the median
    views of the channel's recent long-form (and shorts) uploads. Cached 24h. `ident` is a
    UC id or an @handle. Tracked channels use the store for free; others cost ~3 quota units."""
    ident = (ident or "").strip()
    if not ident:
        return None
    key = "baseline:" + ident.lower()
    if not force:
        c = _cache_get(store, key, BASELINE_TTL)
        if c is not None:
            return c
    cid = None
    m = _UC_RE.search(ident)
    if m:
        cid = m.group(1)
    # tracked channels: median straight from the store, no API call
    if cid:
        row = store.channel(cid)
        if row:
            st = json.loads(row.get("stats_json") or "{}") if row.get("stats_json") else {}
            base = _baseline_from(cid, {**row, **st}, store.videos(cid) or [])
            if base.get("sample"):
                _cache_put(store, key, base)
                return base
    if not yt:
        return None
    if cid:
        chans = yt.channels_by_ids([cid])
        ch = chans[0] if chans else None
    else:
        ch = yt.channel_by_handle(ident.lstrip("@"))
    if not ch:
        base = {"missing": True}
        _cache_put(store, key, base)
        return base
    vids = []
    if ch.get("uploads_playlist"):
        ids = yt.playlist_video_ids(ch["uploads_playlist"], limit=25)
        if ids:
            vids = yt.videos(ids)
    base = _baseline_from(ch["channel_id"], ch, vids)
    _cache_put(store, key, base)
    return base


def _delta(rows, n):
    """Delta between the latest snapshot and the newest one at least n days older.
    n falsy -> the full span we hold (the closest thing to a 'since tracking' figure)."""
    if len(rows) < 2:
        return None
    latest = rows[-1]
    cutoff = None
    for r in rows[:-1]:
        d = (datetime.strptime(latest["day"], "%Y-%m-%d") - datetime.strptime(r["day"], "%Y-%m-%d")).days
        if not n or d >= n:
            cutoff = r
            if not n:  # oldest we have
                break
    if not cutoff or cutoff is latest:
        return None
    return {"days": (datetime.strptime(latest["day"], "%Y-%m-%d") - datetime.strptime(cutoff["day"], "%Y-%m-%d")).days,
            "since": cutoff["day"],
            "subscribers": (latest["subscribers"] or 0) - (cutoff["subscribers"] or 0),
            "views": (latest["views"] or 0) - (cutoff["views"] or 0),
            "videos": (latest["videos"] or 0) - (cutoff["videos"] or 0)}


def window_growth(store, channel_id, days):
    """Sub/view/video delta for a period, from the daily snapshots. days falsy = whole
    span held. Returns None until at least two days of snapshots exist (fills in over time)."""
    rows = snapshots(store, channel_id, 800)
    return _delta(rows, days or None) if rows else None


def growth(store, channel_id):
    """Deltas between the snapshots we hold (fills in as days pass)."""
    rows = snapshots(store, channel_id, 400)
    if not rows:
        return {"days": 0, "rows": []}
    latest = rows[-1]
    return {"days": len(rows), "first": rows[0]["day"], "last": latest["day"], "rows": rows[-90:],
            "d7": _delta(rows, 7), "d30": _delta(rows, 30)}


# ---------------------------------------------------------------- per-video report (extension)
VIDEO_TTL = 1800

# Rough revenue bands, USD per 1000 *total* views (already discounted for the monetized
# fraction / ad fill — these are deliberately conservative "estimate" numbers, like the
# figures ViewStats shows). Longform earns from ads; Shorts from the Shorts pool (far less).
RPM_LONG = (0.8, 2.0, 4.5)
RPM_SHORT = (0.02, 0.05, 0.12)


def _rank_10(value, pool):
    """Placement of `value` on a 1..10 scale within `pool` (this channel's recent uploads):
    10 = better than (nearly) all of them, 1 = at the bottom. Returns (score, percentile, n)."""
    others = [v for v in pool if v is not None]
    if not others:
        return None
    n = len(others)
    at_or_below = sum(1 for v in others if v <= value)
    pct = at_or_below / n                      # 0..1, this video included
    score = max(1, min(10, int(pct * 10 + 0.999)))
    return {"score": score, "percentile": round(pct * 100), "sample": n}


def video_report(store, yt, video_id, channel_id=None, force=False):
    """Everything the watch-page panel shows for one video: its own numbers, the channel
    overview, where it places on a 1..10 scale for the channel, its outlier multiple, and a
    rough estimated-revenue band. Cached 30 min. Costs ~1 unit for the video + the channel
    lookup (itself cached 1h) the first time a channel is seen."""
    video_id = (video_id or "").strip()
    if not video_id:
        return None
    key = "vreport:" + video_id
    if not force:
        c = _cache_get(store, key, VIDEO_TTL)
        if c is not None:
            c["cached"] = True
            return c
    if not yt:
        return {"error": "no_api"}
    vids = yt.videos([video_id])
    if not vids:
        return {"error": "not_found"}
    v = vids[0]
    cid = channel_id or v.get("channel_id")
    now = datetime.now(timezone.utc)
    ts = _parse_ts(v.get("published_at") or "")
    age_d = max(1e-3, (now - ts).total_seconds() / 86400) if ts else None
    views = v.get("views") or 0
    is_short = bool(int(v.get("is_short") or 0))
    likes = v.get("likes") or 0
    comments = v.get("comments") or 0
    video = {
        "id": video_id, "title": v.get("title"), "views": views, "likes": likes,
        "comments": comments, "published_at": v.get("published_at"),
        "age_days": round(age_d, 1) if age_d else None,
        "views_per_day": round(views / age_d) if age_d else None,
        "is_short": is_short, "duration_s": v.get("duration_s"), "thumb": v.get("thumb"),
        "engagement": round(100 * (likes + comments) / views, 2) if views else None,
        "like_rate": round(100 * likes / views, 2) if views else None,
    }
    channel, metric, rank, outlier = None, None, None, None
    if cid:
        try:
            look = lookup_channel(store, yt, cid, n_videos=30)
        except Exception:
            look = None
        if look:
            ch = look.get("channel") or {}
            channel = {"id": cid, "title": ch.get("title"), "handle": ch.get("handle"),
                       "subs": ch.get("subscribers"), "views": ch.get("views"),
                       "videos": ch.get("videos"), "thumb": ch.get("thumb"),
                       "age_days": (look.get("metrics") or {}).get("channel_age_days")}
            metric = look.get("metrics") or {}
            pool_vids = look.get("videos") or []
            # rank against same-format uploads (shorts vs longform); include this video
            same_objs = [pv for pv in pool_vids
                         if bool(int(pv.get("is_short") or 0)) == is_short]
            if not same_objs:
                same_objs = list(pool_vids)
            if all(pv.get("video_id") != video_id for pv in same_objs):
                same_objs = same_objs + [{
                    "video_id": video_id, "title": video["title"], "views": views,
                    "is_short": is_short, "published_at": video["published_at"],
                    "age_days": video["age_days"], "views_per_day": video["views_per_day"],
                    "thumb": video["thumb"],
                }]
            same = [pv.get("views") or 0 for pv in same_objs]
            rank = _rank_10(views, same)
            if rank:
                ranked = sorted(same_objs, key=lambda pv: pv.get("views") or 0, reverse=True)
                cur_i = next((i for i, pv in enumerate(ranked)
                              if pv.get("video_id") == video_id), None)
                rank["position"] = (cur_i + 1) if cur_i is not None else None
                rank["list"] = [{
                    "rank": i + 1,
                    "video_id": pv.get("video_id"),
                    "title": pv.get("title") or "(untitled)",
                    "views": pv.get("views") or 0,
                    "views_per_day": pv.get("views_per_day"),
                    "age_days": pv.get("age_days"),
                    "thumb": pv.get("thumb"),
                    "is_short": bool(int(pv.get("is_short") or 0)),
                    "current": pv.get("video_id") == video_id,
                } for i, pv in enumerate(ranked)]
            med = metric.get("median_views")
            if not is_short and med:
                outlier = round(views / med, 2)
            elif same:
                base = statistics.median(same) if same else 0
                outlier = round(views / base, 2) if base else None
    rpm = RPM_SHORT if is_short else RPM_LONG
    revenue = {"low": round(views / 1000 * rpm[0], 2), "mid": round(views / 1000 * rpm[1], 2),
               "high": round(views / 1000 * rpm[2], 2),
               "rpm_low": rpm[0], "rpm_high": rpm[2], "basis": "shorts" if is_short else "longform"}
    data = {"video": video, "channel": channel, "metrics": metric, "rank": rank,
            "outlier": outlier, "revenue": revenue, "fetched_at": time.time()}
    _cache_put(store, key, data)
    return data
