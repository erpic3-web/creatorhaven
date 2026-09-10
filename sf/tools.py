"""The public web tools. Each function takes plain inputs and returns JSON-able
dicts; app.py exposes them as POST /api/tools/<name>. `yt` is a Data API client
(OAuth token from any linked channel, or an API key), `cfg` the config.
"""
import csv
import hashlib
import io
import json
import re
import urllib.parse
from collections import Counter
from datetime import datetime, timezone

from . import gemini, http, innertube, metrics

_VID_PATTERNS = [
    re.compile(r"(?:v=|/shorts/|/embed/|/live/|youtu\.be/|/v/)([A-Za-z0-9_-]{11})"),
    re.compile(r"^([A-Za-z0-9_-]{11})$"),
]
_PL_RE = re.compile(r"(?:list=)([A-Za-z0-9_-]{10,})")
_HANDLE_RE = re.compile(r"(?:youtube\.com/)?@([A-Za-z0-9._-]{3,30})")
_UC_RE = re.compile(r"(UC[A-Za-z0-9_-]{22})")
_CUSTOM_RE = re.compile(r"youtube\.com/(?:c|user)/([A-Za-z0-9._-]+)")

THUMB_SIZES = [("maxresdefault", 1280, 720), ("sddefault", 640, 480), ("hqdefault", 480, 360),
               ("mqdefault", 320, 180), ("default", 120, 90)]
THUMB_BASE = "https://i.ytimg.com"

SPONSORBLOCK = "https://sponsor.ajay.app/api/skipSegments"
SB_CATEGORIES = ["sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "music_offtopic", "filler"]


class ToolError(ValueError):
    pass


# ---------------------------------------------------------------- parsing
def video_id(s):
    s = (s or "").strip()
    for rx in _VID_PATTERNS:
        m = rx.search(s)
        if m:
            return m.group(1)
    raise ToolError("Could not find a video id in that input")


def playlist_id(s):
    s = (s or "").strip()
    m = _PL_RE.search(s)
    if m:
        return m.group(1)
    if re.fullmatch(r"(PL|UU|LL|RD|OL)[A-Za-z0-9_-]{8,}", s):
        return s
    raise ToolError("Could not find a playlist id in that input")


def channel_ref(s):
    """Return ('id'|'handle'|'username', value)."""
    s = (s or "").strip()
    m = _UC_RE.search(s)
    if m:
        return "id", m.group(1)
    m = _HANDLE_RE.search(s)
    if m:
        return "handle", m.group(1)
    m = _CUSTOM_RE.search(s)
    if m:
        return "username", m.group(1)
    if s and " " not in s:
        return "handle", s.lstrip("@")
    raise ToolError("Give a channel URL, @handle or channel id")


def resolve_channel(yt, ref):
    kind, val = channel_ref(ref)
    if kind == "id":
        rows = yt.channels_by_ids([val])
        return rows[0] if rows else None
    if kind == "handle":
        return yt.channel_by_handle(val)
    return yt.channel_by_username(val) or yt.channel_by_handle(val)


# ------------------------------------------------------------- downloaders
def thumbnails(video):
    vid = video_id(video)
    return {"video_id": vid,
            "sizes": [{"name": n, "width": w, "height": h, "url": f"{THUMB_BASE}/vi/{vid}/{n}.jpg"}
                      for n, w, h in THUMB_SIZES],
            "webp": f"{THUMB_BASE}/vi_webp/{vid}/maxresdefault.webp",
            "note": "maxresdefault only exists for uploads that had a 720p+ source; the site checks and marks it."}


def check_thumb_sizes(result):
    """HEAD each size and mark which really exist (maxres is often a 404)."""
    for s in result["sizes"]:
        try:
            body, hdr = http.request(s["url"], method="HEAD", retries=0, timeout=15)
            s["exists"] = True
            s["bytes"] = int(hdr.get("Content-Length") or 0)
        except Exception:
            s["exists"] = False
    return result


def _upscale(url, size=800):
    if not url:
        return None
    return re.sub(r"=s\d+", f"=s{size}", url) if "=s" in url else url


def channel_images(yt, ref):
    ch = resolve_channel(yt, ref)
    if not ch:
        raise ToolError("Channel not found")
    banner = ch.get("banner")
    return {"channel": ch, "profile": {"default": ch.get("thumb"), "large": _upscale(ch.get("thumb"), 800),
                                       "max": _upscale(ch.get("thumb"), 2048)},
            "banner": {"base": banner, "desktop": f"{banner}=w2560-fcrop64=1,00005a57ffffa5a8-k-c0xffffffff-no-nd-rj" if banner else None,
                       "tv": f"{banner}=w2120-fcrop64=1,00000000ffffffff-k-c0xffffffff-no-nd-rj" if banner else None,
                       "mobile": f"{banner}=w1060-fcrop64=1,00005a57ffffa5a8-k-c0xffffffff-no-nd-rj" if banner else None}}


def channel_id_finder(yt, ref):
    ch = resolve_channel(yt, ref)
    if not ch:
        raise ToolError("Channel not found")
    ch["url"] = f"https://www.youtube.com/channel/{ch['channel_id']}"
    ch["rss"] = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['channel_id']}"
    return ch


# ---------------------------------------------------------------- exports
def _csv(rows, fields):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
    return buf.getvalue()


def comment_export(yt, video, limit=1000, order="relevance"):
    vid = video_id(video)
    rows = yt.comment_threads(vid, limit=limit, order=order)
    fields = ["comment_id", "author", "author_channel", "text", "likes", "replies", "published_at"]
    return {"video_id": vid, "count": len(rows), "rows": rows, "csv": _csv(rows, fields)}


def playlist_export(yt, playlist, limit=1000):
    pid = playlist_id(playlist)
    meta = yt.playlist(pid)
    items = yt.playlist_items(pid, limit=limit)
    ids = [i["video_id"] for i in items if i.get("video_id")]
    vids = {v["video_id"]: v for v in yt.videos(ids)}
    rows = []
    for it in items:
        v = vids.get(it["video_id"], {})
        rows.append({"position": it.get("position"), "video_id": it["video_id"], "title": v.get("title") or it.get("title"),
                     "channel": v.get("channel_title") or it.get("channel_title"), "published_at": v.get("published_at"),
                     "duration_s": v.get("duration_s"), "views": v.get("views"), "likes": v.get("likes"),
                     "comments": v.get("comments"), "url": f"https://www.youtube.com/watch?v={it['video_id']}"})
    fields = ["position", "video_id", "title", "channel", "published_at", "duration_s", "views", "likes", "comments", "url"]
    total_views = sum(r["views"] or 0 for r in rows)
    total_s = sum(r["duration_s"] or 0 for r in rows)
    return {"playlist": meta, "count": len(rows), "total_views": total_views, "total_duration_s": total_s,
            "top": sorted(rows, key=lambda r: r["views"] or 0, reverse=True)[:10], "rows": rows,
            "csv": _csv(rows, fields)}


def channel_backup(yt, channel, limit=2000):
    ch = resolve_channel(yt, channel)
    if not ch or not ch.get("uploads_playlist"):
        raise ToolError("Channel not found")
    ids = yt.playlist_video_ids(ch["uploads_playlist"], limit=limit)
    vids = yt.videos(ids)
    fields = ["video_id", "title", "published_at", "privacy", "publish_at", "duration_s", "is_short", "views",
              "likes", "comments", "made_for_kids", "yt_rating", "category_id", "tags", "description"]
    rows = [dict(v, tags="|".join(v.get("tags") or [])) for v in vids]
    return {"channel": ch, "count": len(vids), "csv": _csv(rows, fields), "json": vids}


# --------------------------------------------------------------- analyzers
def _estimate_retention(duration_s, like_rate, comment_rate, is_short):
    """A deterministic, YouTube-shaped audience-retention (AVD) estimate from PUBLIC signals only.
    Real AVD/retention is private to the channel owner; this is a modelled estimate — a sharp hook
    drop then a smooth decay, shaped by video length and engagement (higher engagement holds longer)."""
    import math
    d = max(1.0, float(duration_s or 1))
    eng = (like_rate or 0.0) + 4.0 * (comment_rate or 0.0)   # combined engagement signal
    if is_short:
        hook, end, k = 0.90, 0.55, 0.9
    else:
        length_pen = min(0.24, math.log10(max(1.0, d / 60.0)) * 0.10)   # longer videos shed more
        hook = max(0.55, 0.82 - length_pen + min(0.10, eng * 6.0))
        end = max(0.20, 0.42 - length_pen * 1.4 + min(0.12, eng * 8.0))
        k = 1.25
    curve = []
    for i in range(0, 101, 5):
        p = i / 100.0
        if p <= 0.12:                                  # the hook window
            ret = 1.0 - (1.0 - hook) * (p / 0.12)
        else:                                          # smooth decay hook -> end
            x = (p - 0.12) / 0.88
            ret = hook - (hook - end) * (1 - math.exp(-k * x)) / (1 - math.exp(-k))
        curve.append({"pct": i, "retention": round(max(0.0, min(1.0, ret)) * 100, 1)})
    avd_pct = round(sum(c["retention"] for c in curve) / len(curve), 1)   # mean ≈ area under the curve
    return {"curve": curve, "avd_pct": avd_pct, "avd_seconds": int(d * avd_pct / 100.0),
            "hook_pct": round(hook * 100, 1), "end_pct": round(end * 100, 1)}


def _estimate_ctr(views_per_day, like_rate, is_short):
    """A rough estimated CTR band from packaging proxies (engagement + velocity). Impressions and
    real CTR are owner-only; this is explicitly an estimate, never a measured number."""
    base = 4.0
    if like_rate:
        base += min(3.0, like_rate * 100 * 0.6)
    if views_per_day and views_per_day > 50000:
        base += 2.0
    elif views_per_day and views_per_day > 5000:
        base += 1.0
    if is_short:
        base += 1.5
    return {"low": round(max(1.5, base - 1.8), 1), "high": round(base + 1.8, 1),
            "basis": "packaging proxy (engagement + velocity) — estimated, not measured"}


def video_analyzer(yt, video):
    vid = video_id(video)
    vids = yt.videos([vid])
    if not vids:
        raise ToolError("Video not found")
    v = vids[0]
    age = metrics.days_since(v.get("published_at")) or 0.5
    views = v.get("views") or 0
    like_rate = ((v.get("likes") or 0) / views) if views else None
    comment_rate = ((v.get("comments") or 0) / views) if views else None
    vpd = round(views / max(age, 0.5), 1)
    is_short = bool(v.get("is_short"))
    dur = v.get("duration_s") or 0
    ret = _estimate_retention(dur, like_rate, comment_rate, is_short)
    eng_ratio = round(min(0.96, 0.55 + ret["avd_pct"] / 300.0), 3)     # estimated engaged-view share
    out = {"video": v, "age_days": round(age, 1), "views_per_day": vpd,
           "like_rate": round(like_rate, 4) if like_rate is not None else None,
           "comment_rate": round(comment_rate, 4) if comment_rate is not None else None,
           "engagement_rate": round(((v.get("likes") or 0) + (v.get("comments") or 0)) / views, 4) if views else None,
           "format": "Short" if is_short else ("Live" if v.get("was_live") else "Long-form"),
           "retention": ret,
           "ctr_est": _estimate_ctr(vpd, like_rate, is_short),
           "engaged_ratio_est": eng_ratio,
           "engaged_views_est": int(views * eng_ratio) if views else None,
           "estimated_note": "AVD, retention, CTR and engaged views are MODELLED estimates from public data — real values are private to the channel owner (link the channel for exact Analytics).",
           "url": f"https://www.youtube.com/watch?v={vid}"}
    try:
        sig = innertube.monetization_signals(innertube.player(vid))
        out["signals"] = sig
        if not v.get("tags") and sig.get("keywords"):
            out["video"]["tags"] = sig["keywords"]
    except Exception as e:  # public signal is optional
        out["signals_error"] = str(e)[:200]
    return out


def playlist_analyzer(yt, playlist, limit=500):
    return playlist_export(yt, playlist, limit)


def monetization_check(video):
    vid = video_id(video)
    resp = innertube.player(vid)
    return innertube.monetization_signals(resp)


def keyword_analyzer(yt, keyword, region=None):
    """Competition read: who ranks for this term now, how big they are, how fresh."""
    res = yt.search(q=keyword, limit=50, region=region)
    ids = [r["video_id"] for r in res if r.get("video_id")]
    vids = {v["video_id"]: v for v in yt.videos(ids)}
    ch_ids = list({r["channel_id"] for r in res if r.get("channel_id")})
    chans = {c["channel_id"]: c for c in yt.channels_by_ids(ch_ids)} if ch_ids else {}
    rows = []
    for i, r in enumerate(res, 1):
        v = vids.get(r["video_id"], {})
        c = chans.get(r.get("channel_id"), {})
        rows.append({"rank": i, "video_id": r["video_id"], "title": r["title"], "channel": r["channel_title"],
                     "subscribers": c.get("subscribers"), "views": v.get("views"),
                     "published_at": v.get("published_at"), "age_days": round(metrics.days_since(v.get("published_at")) or 0),
                     "duration_s": v.get("duration_s"), "is_short": v.get("is_short")})
    subs = [r["subscribers"] for r in rows if r["subscribers"] is not None]
    views = [r["views"] for r in rows if r["views"] is not None]
    small = sum(1 for s in subs if s < 10000)
    fresh = sum(1 for r in rows if r["age_days"] <= 30)
    top10_med_subs = metrics.median([r["subscribers"] for r in rows[:10] if r["subscribers"] is not None] or [0])
    difficulty = min(100, int(20 + (0 if not subs else min(60, (top10_med_subs ** 0.5) / 20)) - small * 1.5 + (10 if fresh < 5 else 0)))
    return {"keyword": keyword, "results": rows, "median_subscribers_top10": top10_med_subs,
            "median_views": metrics.median(views) if views else 0, "small_channels_in_top50": small,
            "fresh_last30d": fresh, "difficulty": max(0, difficulty),
            "verdict": ("Open — small channels rank here" if difficulty < 40 else
                        "Competitive — needs a strong package" if difficulty < 70 else "Dominated by big channels"),
            "titles_words": Counter(w.lower() for r in rows for w in re.findall(r"[A-Za-z']{4,}", r["title"] or "")).most_common(15)}


def rank_checker(yt, keyword, target, region=None, limit=100):
    """Where does a video or channel rank in YouTube search for a keyword (top 100)."""
    kind = "channel"
    try:
        tgt = video_id(target)
        kind = "video"
    except ToolError:
        ch = resolve_channel(yt, target)
        if not ch:
            raise ToolError("Target channel not found")
        tgt = ch["channel_id"]
    res = yt.search(q=keyword, limit=limit, region=region)
    rank = None
    for i, r in enumerate(res, 1):
        if (kind == "video" and r.get("video_id") == tgt) or (kind == "channel" and r.get("channel_id") == tgt):
            rank = i
            break
    return {"keyword": keyword, "target": tgt, "kind": kind, "rank": rank, "checked": len(res),
            "top": res[:10]}


def tag_rank_checker(yt, video, region=None, max_tags=15):
    vid = video_id(video)
    vids = yt.videos([vid])
    if not vids:
        raise ToolError("Video not found")
    tags = list(vids[0].get("tags") or [])
    if not tags:
        try:
            tags = innertube.monetization_signals(innertube.player(vid)).get("keywords") or []
        except Exception:
            tags = []
    out = []
    for t in tags[:max_tags]:
        res = yt.search(q=t, limit=50, region=region)
        rank = next((i for i, r in enumerate(res, 1) if r.get("video_id") == vid), None)
        out.append({"tag": t, "rank": rank})
    return {"video_id": vid, "title": vids[0].get("title"), "tags_checked": len(out), "tags_total": len(tags),
            "ranks": out, "quota_note": f"{len(out) * 100} quota units used (100 per tag)."}


def sponsor_locator(video):
    vid = video_id(video)
    url = f"{SPONSORBLOCK}?videoID={vid}&categories={urllib.parse.quote(json.dumps(SB_CATEGORIES))}"
    try:
        segs = http.get_json(url, retries=0, timeout=20)
    except http.HttpError as e:
        if e.status == 404:
            return {"video_id": vid, "segments": [], "note": "No submitted segments for this video."}
        raise
    rows = [{"category": s.get("category"), "start": round(s["segment"][0], 1), "end": round(s["segment"][1], 1),
             "duration": round(s["segment"][1] - s["segment"][0], 1), "votes": s.get("votes"), "locked": s.get("locked"),
             "link": f"https://www.youtube.com/watch?v={vid}&t={int(s['segment'][0])}s"} for s in segs]
    rows.sort(key=lambda r: r["start"])
    return {"video_id": vid, "segments": rows, "source": "SponsorBlock (community submitted)"}


def comment_picker(yt, video, winners=1, min_likes=0, unique_authors=True, keyword=None, seed=None):
    vid = video_id(video)
    rows = yt.comment_threads(vid, limit=2000, order="time")
    pool = [r for r in rows if (r.get("likes") or 0) >= min_likes and
            (not keyword or keyword.lower() in (r.get("text") or "").lower())]
    if unique_authors:
        seen, uniq = set(), []
        for r in pool:
            k = r.get("author_channel") or r.get("author")
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        pool = uniq
    seed = seed or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    scored = sorted(pool, key=lambda r: hashlib.sha256(f"{seed}:{r['comment_id']}".encode()).hexdigest())
    return {"video_id": vid, "eligible": len(pool), "seed": seed, "winners": scored[:max(1, int(winners))]}


def subscribe_link(ref):
    kind, val = channel_ref(ref)
    if kind == "id":
        base = f"https://www.youtube.com/channel/{val}"
    elif kind == "handle":
        base = f"https://www.youtube.com/@{val}"
    else:
        base = f"https://www.youtube.com/c/{val}"
    return {"link": base + "?sub_confirmation=1", "base": base}


def superchat_summary(events):
    by_day, by_cur = {}, {}
    for e in events:
        day = (e.get("created_at") or "")[:10]
        by_day[day] = by_day.get(day, 0) + e["amount"]
        by_cur[e.get("currency")] = by_cur.get(e.get("currency"), 0) + e["amount"]
    total = sum(e["amount"] for e in events)
    return {"events": len(events), "total": round(total, 2), "by_currency": {k: round(v, 2) for k, v in by_cur.items()},
            "by_day": [{"day": d, "amount": round(a, 2)} for d, a in sorted(by_day.items())],
            "creator_take_home": round(total * 0.7, 2), "note": "YouTube keeps ~30% of Super Chat/Stickers; taxes and currency not applied.",
            "top": sorted(events, key=lambda e: -e["amount"])[:10]}


# ---------------------------------------------------------- Gemini-backed
def tag_generator(cfg, title, description="", niche="", count=25):
    prompt = f"""You generate YouTube tags. Video title: "{title}".
Description: "{description[:800]}". Niche/context: "{niche}".
Return JSON: {{"tags": [..up to {count} strings, most important first, mix of exact-topic, broad-topic and long-tail, no hashtags..],
"title_keywords": [..3-6 words the title should contain..], "note": "one sentence of advice"}}.
Rules: lowercase except proper nouns, no duplicates, each tag 1-4 words, total under 500 characters."""
    j = gemini.generate(cfg["gemini_api_key"], prompt, json_mode=True)
    tags = [t.strip() for t in j.get("tags", []) if isinstance(t, str) and t.strip()]
    total = 0
    kept = []
    for t in tags:
        if total + len(t) + 1 > 500:
            break
        kept.append(t)
        total += len(t) + 1
    return {"tags": kept, "paste": ",".join(kept), "chars": total, "title_keywords": j.get("title_keywords", []),
            "note": j.get("note", "")}


def ad_safety(cfg, text):
    prompt = f"""You are a YouTube advertiser-friendliness reviewer. Review this title/description/script text
for anything that commonly limits or removes ads: profanity, violence, adult themes, drugs, dangerous acts,
harmful or hateful content, shocking content, controversial issues, firearms, tobacco, and misleading metadata.
Text: <<<{text[:6000]}>>>
Return JSON: {{"risk": "low"|"medium"|"high", "score": 0-100, "flags": [{{"quote": "...", "issue": "...", "fix": "..."}}],
"summary": "two sentences", "safer_title": "optional rewrite if the title itself is the problem, else empty"}}."""
    return gemini.generate(cfg["gemini_api_key"], prompt, json_mode=True)


def thumbnail_analyzer(cfg, image_bytes, mime="image/jpeg", title=""):
    from PIL import Image, ImageStat  # optional dependency, present on this machine
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    gray = img.convert("L")
    st = ImageStat.Stat(gray)
    contrast = round(st.stddev[0] / 128, 3)
    small = img.resize((64, 36))
    hsv = small.convert("HSV")
    sat = ImageStat.Stat(hsv.getchannel(1)).mean[0] / 255
    colors = Counter(small.getdata()).most_common(5)
    metricsd = {"width": w, "height": h, "aspect": round(w / h, 3), "is_16_9": abs(w / h - 16 / 9) < 0.02,
                "brightness": round(st.mean[0] / 255, 3), "contrast": contrast, "saturation": round(sat, 3),
                "dominant": ["#%02x%02x%02x" % c for c, _ in colors]}
    prompt = f"""Score this YouTube thumbnail for the title "{title}". Return JSON:
{{"clarity": 0-10, "contrast": 0-10, "color": 0-10, "text_readability": 0-10, "emotion": 0-10, "overall": 0-100,
"what_works": ["..."], "fix_first": ["..."], "text_detected": "...", "small_size_read": "what a viewer sees at 160px wide"}}."""
    try:
        ai = gemini.generate(cfg["gemini_api_key"], prompt, images=[(image_bytes, mime)], json_mode=True)
    except gemini.GeminiError as e:
        ai = {"error": str(e)}
    return {"metrics": metricsd, "ai": ai}


def find_replace_preview(videos, find, replace, in_title=True, in_description=True, case_sensitive=False):
    flags = 0 if case_sensitive else re.I
    rx = re.compile(re.escape(find), flags)
    out = []
    for v in videos:
        change = {}
        if in_title and v.get("title") and rx.search(v["title"]):
            change["title"] = rx.sub(replace, v["title"])
        if in_description and v.get("description") and rx.search(v["description"]):
            change["description"] = rx.sub(replace, v["description"])
        if change:
            out.append({"video_id": v["video_id"], "title": v.get("title"), "changes": change})
    return out
