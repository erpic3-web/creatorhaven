"""Read-only calls to YouTube's public web player endpoint (the same request the
watch page makes). Used for signals the Data API does not expose for videos you
do not own: ad placements (monetization signal), the public keyword list and
the live-chat replay continuation for donation counting.

Works from a residential IP; datacenter IPs may get bot-checked, so the site
runs this locally and the extension can do it in the browser.
"""
import json
import re

from . import http

PLAYER_URL = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
WEB_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"     # public key embedded in YouTube's own web client
CLIENT = {"clientName": "WEB", "clientVersion": "2.20250901.00.00", "hl": "en", "gl": "US"}


def player(video_id, player_url=None):
    player_url = player_url or PLAYER_URL
    payload = {"context": {"client": CLIENT}, "videoId": video_id, "contentCheckOk": True, "racyCheckOk": True}
    sep = "&" if "?" in player_url else "?"
    return http.post_json(f"{player_url}{sep}key={WEB_KEY}", payload,
                          headers={"X-YouTube-Client-Name": "1", "X-YouTube-Client-Version": CLIENT["clientVersion"],
                                   "Origin": "https://www.youtube.com", "Referer": "https://www.youtube.com/"},
                          timeout=30, retries=1)


def monetization_signals(resp):
    """Interpret a player response: ads present => monetized (best-effort public signal)."""
    ads = resp.get("adPlacements") or resp.get("adSlots") or []
    player_ads = resp.get("playerAds") or []
    vd = resp.get("videoDetails") or {}
    mf = (resp.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    ps = resp.get("playabilityStatus") or {}
    return {
        "video_id": vd.get("videoId"),
        "title": vd.get("title"),
        "channel": vd.get("author"),
        "channel_id": vd.get("channelId"),
        "length_s": int(vd.get("lengthSeconds") or 0),
        "is_live": bool(vd.get("isLiveContent")),
        "playable": ps.get("status") == "OK",
        "playability": ps.get("reason") or ps.get("status"),
        "monetized": bool(ads) or bool(player_ads),
        "ad_slots": len(ads),
        "player_ads": len(player_ads),
        "keywords": vd.get("keywords") or [],
        "category": mf.get("category"),
        "family_safe": mf.get("isFamilySafe"),
        "unlisted": mf.get("isUnlisted"),
        "countries": len(mf.get("availableCountries") or []),
        "publish_date": mf.get("publishDate"),
        "upload_date": mf.get("uploadDate"),
        "views_public": int(vd.get("viewCount") or 0) if str(vd.get("viewCount", "")).isdigit() else None,
    }


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def is_video_id(s):
    return bool(s and _ID_RE.match(s))


def keys_matching(obj, pattern, max_nodes=5000, depth=8):
    """Walk a JSON object collecting keys that match a regex (used by tests/probes)."""
    rx = re.compile(pattern, re.I)
    out, seen = [], 0
    stack = [(obj, "$", 0)]
    while stack and seen < max_nodes:
        cur, path, d = stack.pop()
        seen += 1
        if isinstance(cur, dict):
            for k, v in cur.items():
                p = f"{path}.{k}"
                if rx.search(k) and (isinstance(v, (int, float)) or (isinstance(v, str) and len(v) <= 40)):
                    out.append({"key": k, "path": p, "value": v})
                if d < depth and isinstance(v, (dict, list)):
                    stack.append((v, p, d + 1))
        elif isinstance(cur, list):
            for i, v in enumerate(cur[:200]):
                if d < depth and isinstance(v, (dict, list)):
                    stack.append((v, f"{path}[{i}]", d + 1))
    return out


def dumps(o):
    return json.dumps(o, ensure_ascii=False)
