"""Creator alerts computed from the cached video/channel state after each sync.

Each rule yields dicts with a stable `key` so re-scans never duplicate; the
store's add_alert() rejects known keys. Delivery = in-app plus an optional
Discord webhook (the schedule bot already posts there).
"""
import json
import time

from . import http, metrics

KINDS = {
    "age_restricted": ("warn", "Age-restricted"),
    "made_for_kids": ("warn", "Made for kids"),
    "privacy_changed": ("warn", "Visibility changed"),
    "upload_failed": ("error", "Upload problem"),
    "scheduled_no_description": ("info", "Scheduled without description"),
    "low_stock": ("warn", "Schedule running low"),
    "sub_milestone": ("good", "Subscriber milestone"),
    "view_milestone": ("good", "Video milestone"),
    "sync_error": ("error", "Sync failed"),
}


def _watch(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


def _studio(channel_id, video_id=None):
    if video_id:
        return f"https://studio.youtube.com/video/{video_id}/edit"
    return f"https://studio.youtube.com/channel/{channel_id}"


def scan_channel(store, channel, videos, prev_state, now=None):
    """Return (alerts, new_state). prev_state is the per-channel JSON from the last scan."""
    now = now or time.time()
    cid = channel["channel_id"]
    title = channel.get("title") or cid
    prev_videos = prev_state.get("videos", {})
    first_scan = not prev_state
    alerts = []
    state_videos = {}

    for v in videos:
        vid = v["video_id"]
        snap = {"privacy": v.get("privacy"), "yt_rating": v.get("yt_rating"), "mfk": int(v.get("made_for_kids") or 0),
                "upload": v.get("upload_status"), "views": v.get("views")}
        state_videos[vid] = snap
        p = prev_videos.get(vid)
        if first_scan:
            continue   # baseline only: never spam a fresh install with the whole back catalogue
        if snap["yt_rating"] == "ytAgeRestricted" and (not p or p.get("yt_rating") != "ytAgeRestricted"):
            alerts.append({"key": f"age:{vid}", "kind": "age_restricted", "channel_id": cid, "video_id": vid,
                           "title": f"{title}: age restriction on “{v.get('title')}”",
                           "message": "YouTube marked this video 18+.", "link": _studio(cid, vid)})
        if snap["mfk"] and (not p or not p.get("mfk")):
            alerts.append({"key": f"mfk:{vid}", "kind": "made_for_kids", "channel_id": cid, "video_id": vid,
                           "title": f"{title}: “{v.get('title')}” is now Made for kids",
                           "message": "Made-for-kids videos lose personalised ads, comments and notifications.",
                           "link": _studio(cid, vid)})
        if p and p.get("privacy") == "public" and snap["privacy"] in ("private", "unlisted"):
            alerts.append({"key": f"priv:{vid}:{snap['privacy']}", "kind": "privacy_changed", "channel_id": cid,
                           "video_id": vid, "title": f"{title}: “{v.get('title')}” went {snap['privacy']}",
                           "message": "A public video became non-public. If you did not do this, check for a strike or claim.",
                           "link": _studio(cid, vid)})
        if snap["upload"] in ("failed", "rejected") and (not p or p.get("upload") != snap["upload"]):
            alerts.append({"key": f"upl:{vid}", "kind": "upload_failed", "channel_id": cid, "video_id": vid,
                           "title": f"{title}: upload {snap['upload']} for “{v.get('title')}”",
                           "message": "YouTube reports this upload as " + snap["upload"] + ".",
                           "link": _studio(cid, vid)})
        if v.get("views") is not None:
            for m in metrics.milestones_crossed((p or {}).get("views"), v.get("views")):
                alerts.append({"key": f"vm:{vid}:{m}", "kind": "view_milestone", "channel_id": cid, "video_id": vid,
                               "title": f"{title}: “{v.get('title')}” passed {metrics.compact(m)} views",
                               "message": f"Now at {v['views']:,} views.", "link": _watch(vid)})

    # Scheduled videos missing a description (found repeatedly on this network's channels).
    for v in metrics.scheduled(videos):
        if not v.get("description_len"):
            alerts.append({"key": f"nodesc:{v['video_id']}", "kind": "scheduled_no_description", "channel_id": cid,
                           "video_id": v["video_id"], "title": f"{title}: scheduled “{v.get('title')}” has no description",
                           "message": f"Publishes {v['publish_at']}.", "link": _studio(cid, v["video_id"])})

    count = len(metrics.scheduled(videos))
    if count <= metrics.GOOD_OVER and not first_scan:
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        alerts.append({"key": f"stock:{cid}:{day}", "kind": "low_stock", "channel_id": cid,
                       "title": f"{title}: only {count} scheduled", "message": "Below the 7-video buffer.",
                       "link": _studio(cid)})

    subs = (channel.get("stats") or {}).get("subscribers")
    prev_subs = prev_state.get("subscribers")
    for m in metrics.milestones_crossed(prev_subs, subs):
        alerts.append({"key": f"sm:{cid}:{m}", "kind": "sub_milestone", "channel_id": cid,
                       "title": f"{title} passed {metrics.compact(m)} subscribers",
                       "message": f"Now at {subs:,}.", "link": f"https://www.youtube.com/channel/{cid}"})

    for a in alerts:
        sev, _ = KINDS.get(a["kind"], ("info", ""))
        a.setdefault("severity", sev)
        a.setdefault("created_at", now)
    return alerts, {"videos": state_videos, "subscribers": subs, "scanned_at": now}


def run_scan(store, channel, videos, now=None):
    key = f"alert_state:{channel['channel_id']}"
    prev = store.get_setting(key, {}) or {}
    alerts, state = scan_channel(store, channel, videos, prev, now)
    new = [a for a in alerts if store.add_alert(a)]
    store.set_setting(key, state)
    return new


def deliver_discord(store, webhook_url, limit=15):
    """Post undelivered alerts to a Discord webhook as plain text (no embeds)."""
    if not webhook_url:
        return 0
    pending = store.undelivered_alerts()
    if not pending:
        return 0
    lines = []
    for a in pending[:limit]:
        icon = {"error": "🛑", "warn": "⚠️", "good": "🎉"}.get(a["severity"], "ℹ️")
        lines.append(f"{icon} {a['title']}" + (f" — {a['link']}" if a.get("link") else ""))
    if len(pending) > limit:
        lines.append(f"…and {len(pending) - limit} more in CreatorHaven.")
    try:
        http.request(webhook_url, method="POST", data=json.dumps({"content": "\n".join(lines)[:1900]}).encode(),
                     headers={"Content-Type": "application/json"}, retries=1)
    except http.HttpError:
        return 0
    store.mark_delivered([a["id"] for a in pending[:limit]])
    return len(pending[:limit])
