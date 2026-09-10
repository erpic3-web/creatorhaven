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
from datetime import datetime, timezone

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


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_snapshot(store, channel_id, stats):
    """Called on every lookup and every public sync: one row per channel per UTC day."""
    if not channel_id or stats is None:
        return
    _ensure_tables(store)
    store._exec("INSERT OR REPLACE INTO channel_snapshots VALUES (?,?,?,?,?,?)",
                (channel_id, _today(), stats.get("subscribers"), stats.get("views"), stats.get("videos"), time.time()))


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


def lookup_channel(store, yt, channel_id, n_videos=30, force=False):
    key = f"lookup:{channel_id}:{n_videos}"
    if not force:
        cached = _cache_get(store, key, LOOKUP_TTL)
        if cached is not None:
            cached["growth"] = growth(store, channel_id)
            cached["windows"] = _view_windows(cached.get("videos") or [], cached.get("channel") or {})
            cached["tracked"] = channel_id in _tracked_ids(store)
            cached["cached"] = True
            return cached
    chans = yt.channels_by_ids([channel_id])
    if not chans:
        raise ValueError("channel not found")
    ch = chans[0]
    units = 1
    videos = []
    if ch.get("uploads_playlist"):
        ids = yt.playlist_video_ids(ch["uploads_playlist"], limit=n_videos)
        units += max(1, (len(ids) + 49) // 50)
        if ids:
            videos = yt.videos(ids)
            units += max(1, (len(ids) + 49) // 50)
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
    data = {"channel": ch, "metrics": metric, "videos": videos, "top": [v["video_id"] for v in top],
            "units": units, "fetched_at": time.time()}
    _cache_put(store, key, data)
    data["growth"] = growth(store, channel_id)
    data["windows"] = _view_windows(videos, ch)
    data["tracked"] = channel_id in _tracked_ids(store)
    return data


# period selector: channel-wide view estimates (residual decay model) over each window
_WINDOWS = (("28d", 28), ("3mo", 91), ("6mo", 182), ("1yr", 365))


def _view_windows(videos, ch):
    """Accurate 'views in the last N' for a public channel: sum each upload's modelled window
    slice (see metrics.channel_window_views). Uses the per-video outlier score already on each
    video. Estimated from the fetched recent uploads, so it's labelled 'est' in the UI."""
    now = datetime.now(timezone.utc)
    obi = {v["video_id"]: v.get("outlier") for v in videos if v.get("outlier")}
    total_videos = ch.get("videos")
    lifetime_views = ch.get("views")
    created = _parse_ts(ch.get("published_at") or "")
    channel_age_days = (now - created).days if created else None
    out = {}
    for label, d in _WINDOWS:
        est = metrics.channel_window_views(videos, d, now=now, outlier_by_id=obi or None,
                                           total_videos=total_videos, lifetime_views=lifetime_views,
                                           channel_age_days=channel_age_days)
        out[label] = {"days": d, "views": est["views"], "from_uploads": est.get("from_uploads"),
                      "back_catalog": est.get("back_catalog", 0), "max_outlier": est["max_outlier"],
                      "sample": est["sample"]}
    out["lifetime"] = {"days": 0, "views": ch.get("views"), "boost": 0,
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
