"""The OUTLIER RADAR — what the strategy brain reads before it opens its mouth.

Two feeds, both real and id-carrying (the brain is forbidden from citing anything else):

  NETWORK  — every tracked channel's uploads already in the store (~thousands of videos,
             zero quota): each video scored against the median of that channel's previous
             30 uploads of the same format (metrics.outlier_scores). A 4.0 = four times
             what that channel normally does.  Recent (last `days`) + >= min_x only.

  NICHE    — videos BEYOND the tracked channels, found through the YouTube Data API:
             search.list ordered by viewCount inside the last `days` for each niche query
             (100 units a query), videos.list for the real stats, then every unfamiliar
             channel gets a baseline (median of its recent uploads, search.channel_baseline,
             ~3 units, cached 24 h) so the multiple is against ITS OWN normal, not raw
             views.  That is how a 20k-sub channel doing 900k shows up above a 2M-sub
             channel doing 1M — it is the pattern we want, not the size.

Queries come from the network's own tags (counted once per channel so one spammy uploader
can't own the list) unless the operator pins their own list in settings `radar:queries`.
The whole result is cached in settings `radar:latest` and rebuilt on demand.
"""
import math
import random
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from . import metrics
from . import search as _search

LATEST_KEY = "radar:latest"
QUERIES_KEY = "radar:queries"
DEFAULT_DAYS = 45
MIN_X = 2.0

_GENERIC = {"gaming", "vr", "funny", "video", "videos", "youtube", "shorts", "short", "viral",
            "trending", "new", "best", "top", "family friendly", "kids", "game", "games", "reaction", "reactions", "react",
            "virtual reality", "vr gaming", "lol", "meme", "memes", "comedy", "entertainment"}


def _now():
    return datetime.now(timezone.utc)


def _parse(iso):
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _age_days(iso, now=None):
    ts = _parse(iso)
    if not ts:
        return None
    return max(0.05, ((now or _now()) - ts).total_seconds() / 86400)


def _row(v, ch, x, age, source, median=None, query=None):
    views = v.get("views") or 0
    return {
        "video_id": v["video_id"], "title": v.get("title") or "", "channel_id": v.get("channel_id"),
        "channel": (ch or {}).get("title") or v.get("channel_title") or "", "subs": (ch or {}).get("subscribers"),
        "views": views, "outlier": round(x, 2), "age_days": round(age, 1),
        "views_per_day": round(views / age) if age else None, "is_short": int(v.get("is_short") or 0),
        "duration_s": v.get("duration_s"), "median": median,
        "thumb": v.get("thumb") or f"https://i.ytimg.com/vi/{v['video_id']}/mqdefault.jpg",
        "published_at": v.get("published_at"), "source": source, "query": query,
    }


# ------------------------------------------------------------------ network feed (free)
def network_outliers(store, days=DEFAULT_DAYS, min_x=MIN_X, limit=60, per_channel=4, now=None):
    """Recent outliers across every tracked channel, from the store. Zero quota."""
    now = now or _now()
    rows = []
    for c in store.channels():
        st = c.get("stats") or {}
        vids = store.videos(c["channel_id"])
        if not vids:
            continue
        scores = metrics.outlier_scores(vids)
        chan = {"title": c.get("title"), "subscribers": st.get("subscribers")}
        mine = []
        for v in vids:
            x = scores.get(v["video_id"])
            if not x or x < min_x:
                continue
            age = _age_days(v.get("published_at"), now)
            if age is None or age > days:
                continue
            mine.append(_row(v, chan, x, age, "network"))
        mine.sort(key=lambda r: r["outlier"], reverse=True)
        rows.extend(mine[:per_channel])
    rows.sort(key=lambda r: r["outlier"], reverse=True)
    return rows[:limit]


# -------------------------------------------------------------------- niche queries
def niche_queries(store, n=8, recent_days=120):
    """Search terms for the niche, derived from the network's own tags: a tag counts ONCE per
    channel (so 200 identical tag walls from one uploader don't dominate), generic words and
    channel names are dropped, multi-word tags win ties."""
    pinned = store.get_setting(QUERIES_KEY)
    if isinstance(pinned, list) and pinned:
        return [str(q).strip() for q in pinned if str(q).strip()][:12]
    cutoff = (_now() - timedelta(days=recent_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    names = set()
    for c in store.channels():
        for s in (c.get("title") or "", (c.get("handle") or "").lstrip("@")):
            s = s.lower().strip()
            if s:
                names.add(s)
                names.add(s.replace(" ", ""))
    per_tag = defaultdict(set)
    for c in store.channels():
        for v in store.videos(c["channel_id"]):
            if (v.get("published_at") or "") < cutoff:
                continue
            for t in v.get("tags") or []:
                t = t.lower().strip()
                if not t or t in _GENERIC or t in names or t.replace(" ", "") in names or len(t) < 4:
                    continue
                if any(nm and nm in t.replace(" ", "") for nm in names if len(nm) >= 5):
                    continue
                per_tag[t].add(c["channel_id"])
    ranked = sorted(per_tag.items(), key=lambda kv: (len(kv[1]), " " in kv[0], -len(kv[0])), reverse=True)
    out = []
    for tag, chans in ranked:
        if len(chans) < 2 and len(out) >= 3:
            break
        # skip a tag that is a sub/superstring of one already chosen ("gorilla tag" vs "gorilla tag vr")
        if any(tag in o or o in tag for o in out):
            continue
        out.append(tag)
        if len(out) >= n:
            break
    return out


# --------------------------------------------------------------------- niche feed (API)
FORMATS = {"long": ("medium", "long"), "short": ("short",)}   # YouTube's videoDuration buckets


def discover(store, yt, queries, days=30, per_query=30, max_baselines=40, min_x=MIN_X, log=None,
             now=None, formats=("long",), region="US"):
    """Search the niche for the biggest recent videos, then score each against its own
    channel's median. Returns (rows, units_spent).

    `formats`: "long" = 4-20 min + 20 min+ (two 100-unit searches per query — the network's
    story/compilation lane), "short" = under 4 min. Searching by viewCount without a duration
    filter returns the whole platform's viral Shorts (celebrity reacts, dance clips), which is
    noise for a longform network, so longform is the default and Shorts are opt-in."""
    now = now or _now()
    units = 0
    seen = {}
    after = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    buckets = []
    for f in formats or ("long",):
        buckets += list(FORMATS.get(f, ()))
    buckets = buckets or ["medium", "long"]
    for q in queries:
        for dur in buckets:
            try:
                hits = yt.search(q=q, type_="video", order="viewCount", limit=per_query, published_after=after,
                                 region=region, video_duration=dur)
            except Exception as e:
                if log:
                    log(f"[radar] search '{q}' ({dur}) failed: {e}")
                continue
            units += 100
            for h in hits:
                vid = h.get("video_id")
                if vid and vid not in seen:
                    seen[vid] = q
    ids = list(seen)
    if not ids:
        return [], units
    vids = []
    try:
        vids = yt.videos(ids)
        units += max(1, math.ceil(len(ids) / 50))
    except Exception as e:
        if log:
            log(f"[radar] videos.list failed: {e}")
        return [], units
    by_chan = defaultdict(list)
    for v in vids:
        if (v.get("privacy") or "public") != "public":
            continue
        by_chan[v.get("channel_id")].append(v)
    tracked = {c["channel_id"]: c for c in store.channels()}
    # baselines: tracked channels are free; unfamiliar ones cost ~3 units each, biggest first
    order = sorted(by_chan, key=lambda cid: max(v.get("views") or 0 for v in by_chan[cid]), reverse=True)
    baselines = {}
    spent = 0
    for cid in order:
        if not cid:
            continue
        if cid in tracked:
            base = _search.channel_baseline(store, None, cid)
        else:
            if spent >= max_baselines:
                continue
            try:
                base = _search.channel_baseline(store, yt, cid)
            except Exception as e:
                if log:
                    log(f"[radar] baseline {cid} failed: {e}")
                base = None
            spent += 1
            units += 3
        if base and not base.get("missing"):
            baselines[cid] = base
    rows = []
    for cid, group in by_chan.items():
        base = baselines.get(cid)
        if not base:
            continue
        for v in group:
            med = base.get("median_short" if int(v.get("is_short") or 0) else "median_long") or base.get("median")
            if not med:
                continue
            x = (v.get("views") or 0) / med
            if x < min_x:
                continue
            age = _age_days(v.get("published_at"), now) or 1.0
            ch = {"title": base.get("title") or v.get("channel_title"), "subscribers": base.get("subs")}
            r = _row(v, ch, x, age, "niche", median=med, query=seen.get(v["video_id"]))
            r["tracked"] = cid in tracked
            rows.append(r)
    rows.sort(key=lambda r: r["outlier"], reverse=True)
    # at most 3 per channel so the feed stays a feed of PATTERNS, not one channel's catalog
    per = Counter()
    out = []
    for r in rows:
        if per[r["channel_id"]] >= 3:
            continue
        per[r["channel_id"]] += 1
        out.append(r)
    return out[:80], units


def refresh(store, yt, days=DEFAULT_DAYS, queries=None, log=None, niche=True, formats=("long",)):
    """Rebuild both feeds and cache them. `yt` may be None (network feed only)."""
    t0 = time.time()
    net = network_outliers(store, days=days)
    q = [x for x in (queries or []) if x] or niche_queries(store)
    rows, units, err = [], 0, None
    if niche and yt and q:
        try:
            rows, units = discover(store, yt, q, days=min(days, 30), log=log, formats=formats)
        except Exception as e:
            err = str(e)
            if log:
                log(f"[radar] discover failed: {e}")
    elif niche and not yt:
        err = "no YouTube client (link a channel or set a YouTube API key) — niche feed skipped"
    data = {"ts": time.time(), "days": days, "queries": q, "units": units, "network": net, "niche": rows, "formats": list(formats or ("long",)),
            "error": err, "took_s": round(time.time() - t0, 1)}
    store.set_setting(LATEST_KEY, data)
    if log:
        log(f"[radar] {len(net)} network outliers, {len(rows)} niche outliers, {units} units, {data['took_s']}s")
    return data


def latest(store):
    d = store.get_setting(LATEST_KEY)
    return d if isinstance(d, dict) else None


def find(store, video_id):
    """A radar row (either feed) or a store video, for 'convert this one' requests."""
    d = latest(store) or {}
    for key in ("niche", "network"):
        for r in d.get(key) or []:
            if r.get("video_id") == video_id:
                return r
    v = store.video(video_id)
    if v:
        ch = store.channel(v["channel_id"]) or {}
        import json as _json
        st = _json.loads(ch.get("stats_json") or "{}") if ch.get("stats_json") else {}
        scores = metrics.outlier_scores(store.videos(v["channel_id"]))
        return _row(v, {"title": ch.get("title"), "subscribers": st.get("subscribers")},
                    scores.get(video_id) or 1.0, _age_days(v.get("published_at")) or 1.0, "network")
    return None


# ------------------------------------------------------------------- prompt material
def _line(r):
    kind = "short" if r.get("is_short") else "long"
    who = r.get("channel") or "?"
    subs = f" ({metrics.compact(r['subs'])} subs)" if r.get("subs") else ""
    med = f", their normal ~{metrics.compact(r['median'])}" if r.get("median") else ""
    return (f"- {r['outlier']}x | \"{r['title'][:80]}\" — {who}{subs}, {metrics.compact(r.get('views'))} views"
            f"{med}, {r['age_days']:.0f}d old, {kind}, id={r['video_id']}")


def brief(store, focus_id=None, n_net=22, n_niche=22, seed=None, exclude_ids=()):
    """Two id-carrying blocks for the system prompt. A seeded shuffle inside the top tier keeps
    successive calls looking at different outliers (the fix for 'it keeps giving me the same
    ideas') while still only ever showing real, strong ones."""
    d = latest(store)
    if not d:
        return ("(radar not built yet — click Refresh radar; until then only the tracked-channel "
                "digest is available)", "", None)
    rnd = random.Random(seed if seed is not None else time.time())

    def pick(rows, n):
        rows = [r for r in rows if r["video_id"] not in exclude_ids]
        top = rows[: max(n * 2, 12)]
        rnd.shuffle(top)
        chosen = sorted(top[:n], key=lambda r: r["outlier"], reverse=True)
        return chosen

    net = pick(d.get("network") or [], n_net)
    niche = pick([r for r in (d.get("niche") or []) if not r.get("tracked")], n_niche)
    age_h = (time.time() - d.get("ts", 0)) / 3600
    head = f"(radar built {age_h:.0f}h ago over the last {d.get('days')} days; queries: {', '.join(d.get('queries') or [])})"
    net_txt = head + "\n" + ("\n".join(_line(r) for r in net) if net else "- none above 2x in this window")
    niche_txt = ("\n".join(_line(r) for r in niche) if niche
                 else ("- niche feed empty: " + (d.get("error") or "refresh the radar with a YouTube client")))
    return net_txt, niche_txt, d


def stats(store):
    d = latest(store)
    if not d:
        return {"built": False}
    return {"built": True, "ts": d.get("ts"), "days": d.get("days"), "queries": d.get("queries"),
            "units": d.get("units"), "network": len(d.get("network") or []), "niche": len(d.get("niche") or []),
            "error": d.get("error"), "took_s": d.get("took_s")}
