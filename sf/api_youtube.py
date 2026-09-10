"""YouTube Data API v3 client over urllib.

Auth is either an OAuth access token (works for public AND private reads, and
for writes) or an API key (public reads only). Every call reports its quota
cost through `quota_cb(units)` so the site can show a live meter against the
10,000 units/day default.
"""
import re
from datetime import datetime, timezone

from . import http

API_BASE = "https://www.googleapis.com/youtube/v3"

# Quota units per method (YouTube Data API v3 quota calculator).
UNITS = {
    "list": 1, "search": 100, "insert": 50, "update": 50, "delete": 50,
    "captions.list": 50, "channels.update": 50,
}

_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_duration(iso):
    """ISO-8601 duration ("PT1H2M3S") to seconds."""
    if not iso:
        return 0
    m = _DUR.fullmatch(iso)
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


SHORT_MAX_S = 180   # Shorts may be up to 3 minutes since late 2024; the API has no format flag.


def normalize_video(raw):
    sn = raw.get("snippet", {}) or {}
    st = raw.get("status", {}) or {}
    cd = raw.get("contentDetails", {}) or {}
    stt = raw.get("statistics", {}) or {}
    live = raw.get("liveStreamingDetails")
    dur = parse_duration(cd.get("duration"))
    thumbs = sn.get("thumbnails", {}) or {}
    thumb = (thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {}).get("url")
    return {
        "video_id": raw.get("id"),
        "title": sn.get("title"),
        "description_len": len(sn.get("description") or ""),
        "published_at": sn.get("publishedAt"),
        "privacy": st.get("privacyStatus"),
        "publish_at": st.get("publishAt"),
        "upload_status": st.get("uploadStatus"),
        "made_for_kids": 1 if st.get("madeForKids") else 0,
        "yt_rating": (cd.get("contentRating") or {}).get("ytRating"),
        "duration_s": dur,
        "is_short": 1 if (0 < dur <= SHORT_MAX_S and not live) else 0,
        "views": int(stt["viewCount"]) if stt.get("viewCount") is not None else None,
        "likes": int(stt["likeCount"]) if stt.get("likeCount") is not None else None,
        "comments": int(stt["commentCount"]) if stt.get("commentCount") is not None else None,
        "tags": sn.get("tags") or [],
        "thumb": thumb,
        "live": 1 if live else 0,
        "channel_id": sn.get("channelId"),
        "channel_title": sn.get("channelTitle"),
        "category_id": sn.get("categoryId"),
        "description": sn.get("description"),
    }


def normalize_channel(raw):
    sn = raw.get("snippet", {}) or {}
    stt = raw.get("statistics", {}) or {}
    cd = raw.get("contentDetails", {}) or {}
    thumbs = sn.get("thumbnails", {}) or {}
    br = (raw.get("brandingSettings") or {}).get("image", {}) or {}
    return {
        "channel_id": raw.get("id"),
        "title": sn.get("title"),
        "handle": sn.get("customUrl"),
        "description": sn.get("description"),
        "country": sn.get("country"),
        "published_at": sn.get("publishedAt"),
        "thumb": (thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {}).get("url"),
        "banner": br.get("bannerExternalUrl"),
        "uploads_playlist": (cd.get("relatedPlaylists") or {}).get("uploads"),
        "subscribers": int(stt["subscriberCount"]) if stt.get("subscriberCount") is not None else None,
        "hidden_subs": bool(stt.get("hiddenSubscriberCount")),
        "views": int(stt["viewCount"]) if stt.get("viewCount") is not None else None,
        "videos": int(stt["videoCount"]) if stt.get("videoCount") is not None else None,
    }


class YouTube:
    def __init__(self, access_token=None, api_key=None, quota_cb=None, base=API_BASE):
        if not access_token and not api_key:
            raise ValueError("YouTube client needs an access token or an API key")
        self.token = access_token
        self.key = api_key
        self.quota_cb = quota_cb
        self.base = base

    # ------------------------------------------------------------ plumbing
    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _url(self, path, params):
        p = dict(params)
        if self.key and not self.token:
            p["key"] = self.key
        return f"{self.base}/{path}?{http.qs(p)}"

    def _charge(self, units):
        if self.quota_cb:
            try:
                self.quota_cb(units)
            except Exception:
                pass

    def _get(self, path, params, units=1):
        self._charge(units)
        return http.get_json(self._url(path, params), headers=self._headers())

    def _post(self, path, params, payload, units=50, method="POST"):
        self._charge(units)
        return http.post_json(self._url(path, params), payload, headers=self._headers(), method=method)

    def _paged(self, path, params, limit, units=1):
        items, token = [], None
        while len(items) < limit:
            p = dict(params, maxResults=min(50, limit - len(items)))
            if token:
                p["pageToken"] = token
            j = self._get(path, p, units)
            items.extend(j.get("items", []))
            token = j.get("nextPageToken")
            if not token or not j.get("items"):
                break
        return items[:limit]

    # ------------------------------------------------------------- channels
    CH_PARTS = "snippet,statistics,contentDetails,brandingSettings"

    def channel_mine(self):
        j = self._get("channels", {"part": self.CH_PARTS, "mine": "true"})
        items = j.get("items") or []
        if not items:
            raise RuntimeError("Google returned no channel for this login")
        return normalize_channel(items[0])

    def channels_by_ids(self, ids):
        out = []
        for i in range(0, len(ids), 50):
            j = self._get("channels", {"part": self.CH_PARTS, "id": ",".join(ids[i:i + 50])})
            out.extend(normalize_channel(c) for c in j.get("items", []))
        return out

    def channel_by_handle(self, handle):
        h = handle if handle.startswith("@") else "@" + handle
        j = self._get("channels", {"part": self.CH_PARTS, "forHandle": h})
        items = j.get("items") or []
        return normalize_channel(items[0]) if items else None

    def channel_by_username(self, username):
        j = self._get("channels", {"part": self.CH_PARTS, "forUsername": username})
        items = j.get("items") or []
        return normalize_channel(items[0]) if items else None

    # --------------------------------------------------------------- videos
    def playlist_video_ids(self, playlist_id, limit=200):
        items = self._paged("playlistItems", {"part": "contentDetails", "playlistId": playlist_id}, limit)
        return [it["contentDetails"]["videoId"] for it in items if it.get("contentDetails")]

    def playlist_items(self, playlist_id, limit=500):
        items = self._paged("playlistItems", {"part": "snippet,contentDetails", "playlistId": playlist_id}, limit)
        return [{"video_id": it["contentDetails"].get("videoId"), "title": it["snippet"].get("title"),
                 "position": it["snippet"].get("position"),
                 "published_at": it["contentDetails"].get("videoPublishedAt"),
                 "channel_title": it["snippet"].get("videoOwnerChannelTitle")}
                for it in items if it.get("contentDetails")]

    def playlist(self, playlist_id):
        j = self._get("playlists", {"part": "snippet,contentDetails", "id": playlist_id})
        items = j.get("items") or []
        if not items:
            return None
        p = items[0]
        return {"playlist_id": p["id"], "title": p["snippet"].get("title"),
                "channel_title": p["snippet"].get("channelTitle"), "channel_id": p["snippet"].get("channelId"),
                "count": (p.get("contentDetails") or {}).get("itemCount")}

    VIDEO_PARTS = "snippet,status,statistics,contentDetails,liveStreamingDetails"

    def videos(self, ids, parts=VIDEO_PARTS):
        out = []
        for i in range(0, len(ids), 50):
            j = self._get("videos", {"part": parts, "id": ",".join(ids[i:i + 50])})
            out.extend(normalize_video(v) for v in j.get("items", []))
        return out

    def update_video_snippet(self, video_id, title, description, tags, category_id):
        payload = {"id": video_id, "snippet": {"title": title, "description": description,
                                                "tags": tags or [], "categoryId": str(category_id)}}
        return self._post("videos", {"part": "snippet"}, payload, units=UNITS["update"], method="PUT")

    # --------------------------------------------------------------- search
    def search(self, q=None, type_="video", order="relevance", limit=50, channel_id=None,
               published_after=None, region=None, video_duration=None):
        params = {"part": "snippet", "q": q, "type": type_, "order": order, "channelId": channel_id,
                  "publishedAfter": published_after, "regionCode": region, "videoDuration": video_duration}
        items = self._paged("search", params, limit, units=UNITS["search"])
        out = []
        for it in items:
            idd = it.get("id", {}) or {}
            sn = it.get("snippet", {}) or {}
            out.append({"kind": idd.get("kind", "").split("#")[-1], "video_id": idd.get("videoId"),
                        "channel_id": idd.get("channelId") if type_ == "channel" else sn.get("channelId"),
                        "playlist_id": idd.get("playlistId"), "title": sn.get("title"),
                        "channel_title": sn.get("channelTitle"), "published_at": sn.get("publishedAt"),
                        "thumb": ((sn.get("thumbnails") or {}).get("medium") or {}).get("url")})
        return out

    # ------------------------------------------------------------- comments
    def comment_threads(self, video_id, limit=500, order="relevance"):
        items = self._paged("commentThreads", {"part": "snippet,replies", "videoId": video_id,
                                               "order": order, "textFormat": "plainText"}, limit)
        out = []
        for it in items:
            top = (it.get("snippet") or {}).get("topLevelComment", {}) or {}
            s = top.get("snippet", {}) or {}
            out.append({"comment_id": top.get("id"), "author": s.get("authorDisplayName"),
                        "author_channel": (s.get("authorChannelId") or {}).get("value"),
                        "text": s.get("textOriginal") or s.get("textDisplay"), "likes": s.get("likeCount"),
                        "published_at": s.get("publishedAt"), "replies": (it.get("snippet") or {}).get("totalReplyCount", 0)})
        return out

    def reply_comment(self, parent_id, text):
        return self._post("comments", {"part": "snippet"},
                          {"snippet": {"parentId": parent_id, "textOriginal": text}}, units=UNITS["insert"])

    # ----------------------------------------------------------- superchats
    def super_chat_events(self, limit=500):
        items = self._paged("superChatEvents", {"part": "snippet"}, limit)
        out = []
        for it in items:
            s = it.get("snippet", {}) or {}
            out.append({"id": it.get("id"), "created_at": s.get("createdAt"), "currency": s.get("currency"),
                        "amount": int(s.get("amountMicros", 0)) / 1e6, "display": s.get("displayString"),
                        "supporter": (s.get("supporterDetails") or {}).get("displayName"),
                        "sticker": bool(s.get("isSuperStickerEvent")), "comment": s.get("commentText")})
        return out


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
