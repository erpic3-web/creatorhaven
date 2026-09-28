"""Hermetic selftest: a mock Google (OAuth token endpoint, Data API v3, Analytics
API v2), mock Gemini, mock SponsorBlock and mock player endpoint on loopback,
plus a network guard that fails any request to a non-loopback host.

    python test/selftest.py
"""
import base64
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ["SF_NO_PERSIST"] = "1"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows consoles default to cp1252
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sf import alerts, api_analytics, api_youtube, config as sfconfig, gemini, innertube, metrics, oauth, sync, tools  # noqa: E402
from sf.store import Store  # noqa: E402
import app as app_module  # noqa: E402

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


# ------------------------------------------------------------ network guard
_real_urlopen = urllib.request.urlopen


def guarded_urlopen(req, *a, **kw):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    host = urllib.parse.urlparse(url).hostname
    if host not in ("127.0.0.1", "localhost"):
        raise AssertionError(f"selftest tried to reach the internet: {url}")
    return _real_urlopen(req, *a, **kw)


urllib.request.urlopen = guarded_urlopen

# ------------------------------------------------------------- mock Google
VID = lambda i, **kw: dict({  # noqa: E731
    "id": i, "snippet": {"title": f"Video {i}", "description": "desc " * 5, "publishedAt": "2026-08-01T10:00:00Z",
                          "channelId": "UCtest000000000000000001", "channelTitle": "Test Channel", "tags": ["minecraft", "horror"],
                          "categoryId": "20", "thumbnails": {"medium": {"url": f"https://i.ytimg.com/vi/{i}/mqdefault.jpg"}}},
    "status": {"privacyStatus": "public", "uploadStatus": "processed", "madeForKids": False},
    "contentDetails": {"duration": "PT12M3S"}, "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "7"}}, **kw)

STATE = {"videos": {}, "calls": [], "analytics_403": None, "engaged_400": False, "strategy_prompts": [], "predict_prompts": []}


def make_videos():
    vids = []
    for n in range(40):
        i = f"vid{n:08d}"
        v = VID(i)
        v["snippet"]["publishedAt"] = f"2026-0{8 if n < 30 else 9}-{(n % 28) + 1:02d}T10:00:00Z"
        v["statistics"]["viewCount"] = str(1000 + n * 100 if n != 25 else 90000)
        if n % 7 == 0:
            v["contentDetails"]["duration"] = "PT0M45S"
        vids.append(v)
    # scheduled ones
    for n in range(9):
        v = VID(f"sch{n:08d}")
        v["status"] = {"privacyStatus": "private", "uploadStatus": "processed", "madeForKids": False,
                       "publishAt": f"2027-01-{n + 1:02d}T15:00:00Z"}
        if n == 2:
            v["snippet"]["description"] = ""
        vids.append(v)
    return vids


STATE["videos"] = {v["id"]: v for v in make_videos()}


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode() if n else ""

    def do_HEAD(self):
        if "maxresdefault" in self.path:
            self.send_response(404)
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Length", "45000")
            self.end_headers()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        body = self._body()
        STATE["calls"].append(("POST", u.path, self.headers.get("Authorization", "")))
        if u.path == "/token":
            form = dict(urllib.parse.parse_qsl(body))
            if form.get("grant_type") == "authorization_code":
                if form.get("code") == "good-code":
                    return self._send({"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600,
                                       "scope": " ".join(oauth.scopes_for(None))})
                return self._send({"error": "invalid_grant"}, 400)
            if form.get("refresh_token") in ("rt-1", "rt-imported"):
                sc = " ".join(oauth.scopes_for(None)) if form["refresh_token"] == "rt-1" else oauth.SCOPE_READONLY
                return self._send({"access_token": "at-" + form["refresh_token"], "expires_in": 3600, "scope": sc})
            return self._send({"error": "invalid_grant"}, 400)
        if u.path == "/gsi/token":            # "log in with Google" token exchange
            payload = base64.urlsafe_b64encode(json.dumps(
                {"sub": "g-123", "email": "guser@gmail.com", "email_verified": True, "name": "G User",
                 "picture": "https://x/pic.png"}).encode()).rstrip(b"=").decode()
            jwt = "eyJ.".rstrip(".") + "." + payload + ".sig"
            return self._send({"id_token": jwt, "access_token": "gat"})
        if u.path == "/discord/token":        # "log in with Discord" token exchange
            return self._send({"access_token": "dtok", "token_type": "Bearer"})
        if u.path == "/youtube/v3/videos":   # update
            payload = json.loads(body)
            v = STATE["videos"].get(payload["id"])
            if not v:
                return self._send({"error": {"message": "not found"}}, 404)
            v["snippet"]["title"] = payload["snippet"]["title"]
            v["snippet"]["description"] = payload["snippet"]["description"]
            return self._send(v)
        if u.path == "/youtube/v3/comments":
            return self._send({"id": "c-new", "snippet": json.loads(body)["snippet"]})
        if u.path.endswith(":generateContent"):
            prompt = json.loads(body)["contents"][0]["parts"][0]["text"]
            if "head of YouTube strategy" in prompt:
                STATE["strategy_prompts"].append(prompt)
                if "Return JSON ONLY in exactly this shape" in prompt:
                    text = json.dumps({"ideas": [
                        {"title": "I Survived 100 Nights With NO Base", "channel": "Test Channel", "format": "challenge", "mechanism": "restriction/challenge",
                         "hook": "cold open", "why": "vid00000008 did 1200x", "ref_id": "vid00000008", "ref_pattern": "ladder", "thumbnail": "face + base",
                         "thumb_text": "NO BASE", "thumbnail_prompt": "a scared player", "series": "weekly"},
                        {"title": "Bogus Ref Idea", "channel": "Test Channel", "format": "story", "mechanism": "story", "hook": "h", "why": "w",
                         "ref_id": "zzzzzzzzzzz", "thumbnail": "t", "thumb_text": "T", "thumbnail_prompt": "p"}]})
                elif "most instructive outliers" in prompt:
                    text = json.dumps({"picks": [{"video_id": "vid00000008", "title": "Video vid00000008", "channel": "Test Channel", "multiple": "1200x",
                                                  "mechanism": "escalation", "why": "big", "convert": "do it"},
                                                 {"video_id": "fakefakefak", "title": "made up", "channel": "?", "multiple": "9x", "mechanism": "", "why": "", "convert": ""}],
                                      "trends": ["challenges are up"]})
                else:
                    text = ("Here's one built on the big one:\n**The 100 Night Ladder**\n[thumb:vid00000008]\nHook: cold open.\n\n"
                            "[[pitched: The 100 Night Ladder | Second Title]]")
                return self._send({"candidates": [{"content": {"parts": [{"text": text}]}}]})
            if "retention analyst" in prompt:
                STATE["predict_prompts"].append(prompt)
                text = json.dumps({"hook_score": 7, "avd_pct": 42, "predicted_score": 71, "confidence": "medium", "verdict": "solid",
                                   "curve": [{"pct": 0, "retention": 100}, {"pct": 5, "retention": 80}, {"pct": 50, "retention": 45}, {"pct": 100, "retention": 20}],
                                   "drop_offs": [{"pct": 8, "reason": "slow intro"}], "replays": [{"pct": 60, "reason": "twist", "strength": 2}],
                                   "notes": [{"t": 12, "type": "cut", "note": "trim"}, {"t": 3, "type": "hook", "note": "faster"}],
                                   "summary": "ok", "fixes": ["cut intro"], "views_multiple": [0.5, 2.0],
                                   "packaging": {"titles": ["A"], "thumbnails": [{"subject": "face", "text": "NO", "colours": "red"}, "plain concept"]}})
                return self._send({"candidates": [{"content": {"parts": [{"text": text}]}}]})
            if "tags" in prompt and "YouTube tags" in prompt:
                text = json.dumps({"tags": ["minecraft horror", "scary minecraft", "analog horror"] * 2, "title_keywords": ["minecraft"], "note": "ok"})
            elif "advertiser" in prompt:
                text = json.dumps({"risk": "low", "score": 12, "flags": [], "summary": "fine", "safer_title": ""})
            else:
                text = json.dumps({"clarity": 8, "contrast": 7, "color": 6, "text_readability": 9, "emotion": 5, "overall": 74,
                                   "what_works": ["face"], "fix_first": ["bigger text"], "text_detected": "SCARY", "small_size_read": "a face"})
            return self._send({"candidates": [{"content": {"parts": [{"text": text}]}}]})
        if u.path == "/player":
            vid = json.loads(body)["videoId"]
            return self._send({"videoDetails": {"videoId": vid, "title": "T", "author": "A", "channelId": "UC1", "lengthSeconds": "600",
                                                "keywords": ["k1", "k2"], "viewCount": "123"},
                               "playabilityStatus": {"status": "OK"},
                               "adPlacements": [{"adPlacementRenderer": {}}] if vid != "vid00000001" else [],
                               "microformat": {"playerMicroformatRenderer": {"category": "Gaming", "isFamilySafe": True,
                                                                             "availableCountries": ["US", "GB"]}}})
        if u.path == "/hook":
            STATE["discord"] = json.loads(body)
            return self._send({"ok": True}, 204) if False else self._send({})
        return self._send({"error": {"message": f"unknown POST {u.path}"}}, 404)

    do_PUT = do_POST   # videos.update is a PUT

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/discord/me":
            if STATE.get("discord_unverified"):     # someone else's address, never verified
                return self._send({"id": "d-888", "username": "sneaky", "email": "bob@x.com", "verified": False})
            return self._send({"id": "d-777", "username": "erikd", "global_name": "Erik D",
                               "email": "erikd@discord.test", "avatar": "abcavatar", "verified": True})
        q = dict(urllib.parse.parse_qsl(u.query))
        auth = self.headers.get("Authorization", "")
        STATE["calls"].append(("GET", u.path, auth))
        p = u.path
        if p == "/youtube/v3/channels":
            if q.get("mine") == "true" or q.get("id") or q.get("forHandle") or q.get("forUsername"):
                if q.get("forHandle") == "@nobody":
                    return self._send({"items": []})
                cid = q.get("id", "UCtest000000000000000001").split(",")[0]
                if auth == "Bearer at-rt-imported" and q.get("mine") == "true":
                    cid = "UCimported0000000000000001"
                return self._send({"items": [{"id": cid, "snippet": {"title": "Test Channel" if cid.startswith("UCtest") else "Imported Channel", "customUrl": "@testchannel",
                                                                     "publishedAt": "2020-01-01T00:00:00Z", "country": "US",
                                                                     "thumbnails": {"medium": {"url": "https://yt3.ggpht.com/x=s240"}}},
                                              "statistics": {"subscriberCount": "125000", "viewCount": "50000000", "videoCount": "49"},
                                              "contentDetails": {"relatedPlaylists": {"uploads": "UUtest"}},
                                              "brandingSettings": {"image": {"bannerExternalUrl": "https://yt3.googleusercontent.com/banner"}}}]})
        if p == "/youtube/v3/playlistItems":
            vids = list(STATE["videos"].values())
            start = int(q.get("pageToken", "0") or 0)
            page = vids[start:start + 50]
            out = {"items": [{"snippet": {"title": v["snippet"]["title"], "position": i, "videoOwnerChannelTitle": "Test Channel"},
                              "contentDetails": {"videoId": v["id"], "videoPublishedAt": v["snippet"]["publishedAt"]}} for i, v in enumerate(page, start)]}
            if start + 50 < len(vids):
                out["nextPageToken"] = str(start + 50)
            return self._send(out)
        if p == "/youtube/v3/playlists":
            return self._send({"items": [{"id": q["id"], "snippet": {"title": "PL", "channelTitle": "Test Channel", "channelId": "UC1"}, "contentDetails": {"itemCount": 3}}]})
        if p == "/youtube/v3/videos":
            ids = q.get("id", "").split(",")
            return self._send({"items": [STATE["videos"][i] for i in ids if i in STATE["videos"]]})
        if p == "/youtube/v3/search":
            items = []
            for n in range(min(50, int(q.get("maxResults", 50)))):
                items.append({"id": {"kind": "youtube#video", "videoId": f"vid{n:08d}"},
                              "snippet": {"title": f"Video {n} about {q.get('q')}", "channelId": "UCtest000000000000000001", "channelTitle": "Test Channel",
                                          "publishedAt": "2026-08-01T00:00:00Z", "thumbnails": {}}})
            return self._send({"items": items})
        if p == "/youtube/v3/commentThreads":
            items = [{"snippet": {"totalReplyCount": 0, "topLevelComment": {"id": f"c{n}", "snippet": {"authorDisplayName": f"user{n % 5}", "authorChannelId": {"value": f"UCu{n % 5}"},
                                                                                                   "textOriginal": f"comment {n} giveaway", "likeCount": n, "publishedAt": "2026-08-01T00:00:00Z"}}}} for n in range(12)]
            return self._send({"items": items})
        if p == "/youtube/v3/superChatEvents":
            return self._send({"items": [{"id": "s1", "snippet": {"createdAt": "2026-09-01T00:00:00Z", "currency": "USD", "amountMicros": "5000000", "displayString": "$5.00", "supporterDetails": {"displayName": "fan"}}},
                                         {"id": "s2", "snippet": {"createdAt": "2026-09-02T00:00:00Z", "currency": "USD", "amountMicros": "20000000", "displayString": "$20.00", "supporterDetails": {"displayName": "fan2"}}}]})
        if p == "/analytics/v2/reports":
            if STATE["analytics_403"]:
                return self._send({"error": {"code": 403, "message": STATE["analytics_403"][1], "errors": [{"reason": STATE["analytics_403"][0]}]}}, 403)
            metrics_ = q["metrics"].split(",")
            if STATE["engaged_400"] and "engagedViews" in metrics_:
                return self._send({"error": {"code": 400, "message": "Unknown identifier (engagedViews) given in metrics parameter."}}, 400)
            if auth != "Bearer at-rt-1" and auth != "Bearer at-1":
                return self._send({"error": {"code": 403, "message": "Request had insufficient authentication scopes.", "errors": [{"reason": "insufficientPermissions"}]}}, 403)
            dims = q.get("dimensions", "")
            if dims == "day":
                from datetime import date, timedelta
                s, e = date.fromisoformat(q["startDate"]), date.fromisoformat(q["endDate"])
                rows, d, n = [], s, 0
                while d <= e:
                    row = []
                    for m in metrics_:
                        row.append({"views": 1000 + n, "engagedViews": 800 + n, "estimatedMinutesWatched": 5000, "averageViewDuration": 300,
                                    "subscribersGained": 20, "subscribersLost": 5, "likes": 40, "comments": 6, "shares": 3, "estimatedRevenue": 12.5}[m])
                    rows.append([d.isoformat()] + row)
                    d += timedelta(days=1)
                    n += 1
                return self._send({"columnHeaders": [{"name": "day"}] + [{"name": m} for m in metrics_], "rows": rows})
            if dims == "video":
                rows = [[f"vid{n:08d}", 5000 - n * 100, 4000 - n * 80, 900] for n in range(10)]
                return self._send({"columnHeaders": [{"name": "video"}] + [{"name": m} for m in metrics_], "rows": rows})
            return self._send({"columnHeaders": [{"name": m} for m in metrics_], "rows": [[123456, 100000, 5000, 300, 200, 50, 400, 60, 30] + ([99.5] if "estimatedRevenue" in metrics_ else [])]})
        if p == "/sponsorblock/api/skipSegments":
            if "vid00000001" in q.get("videoID", ""):
                self.send_response(404)
                self.end_headers()
                return
            return self._send([{"category": "sponsor", "segment": [30.0, 75.5], "UUID": "x", "votes": 12, "locked": 0},
                               {"category": "selfpromo", "segment": [600.0, 620.0], "UUID": "y", "votes": 3, "locked": 0}])
        return self._send({"error": {"message": f"unknown GET {p}"}}, 404)


def start_mock():
    srv = HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def main():
    srv, base = start_mock()
    tmp = Path(tempfile.mkdtemp())
    sfconfig.DATA = tmp / "data"; sfconfig.DATA.mkdir(parents=True, exist_ok=True)  # keep thumbs/etc. off the real data dir
    cfg = {"site_password": "", "require_login": True, "owner_email": "", "secret_key": "k" * 32,
           "port": 5999, "base_url": "http://127.0.0.1:5999",
           "database": str(tmp / "t.sqlite3"), "gemini_api_key": "gem", "youtube_api_key": "",
           "google": {"client_id": "cid", "client_secret": "csec", "client_type": "desktop"},
           "google_signin": {"client_id": "gsi-id", "client_secret": "gsi-sec"},
           "discord_oauth": {"client_id": "disc-id", "client_secret": "disc-sec"},
           "discord_webhook_url": f"{base}/hook", "scan_per_channel": 200, "analytics_days": 30, "sync_minutes": 0,
           "ext_token": "ext-secret"}
    bases = {"api_base": f"{base}/youtube/v3", "analytics_base": f"{base}/analytics/v2", "token_base": base,
             "auth_base": f"{base}/auth",
             "google_signin_token_base": f"{base}/gsi/token",
             "discord_token_base": f"{base}/discord/token", "discord_me_base": f"{base}/discord/me"}
    gemini.BASE = f"{base}/gemini/v1beta"
    tools.SPONSORBLOCK = f"{base}/sponsorblock/api/skipSegments"
    tools.THUMB_BASE = f"{base}/thumbs"
    innertube.PLAYER_URL = f"{base}/player"
    store = Store(cfg["database"])
    application = app_module.create_app(cfg, store, bases)
    c = application.test_client()

    # ---------------------------------------------------------- pure units
    check("duration parse", api_youtube.parse_duration("PT1H2M3S") == 3723 and api_youtube.parse_duration("PT45S") == 45)
    nv = api_youtube.normalize_video(VID("abc"))
    check("normalize video", nv["duration_s"] == 723 and nv["is_short"] == 0 and nv["views"] == 1000 and nv["tags"] == ["minecraft", "horror"])
    check("short detection", api_youtube.normalize_video(VID("s", contentDetails={"duration": "PT0M59S"}))["is_short"] == 1)
    check("video id parsing", tools.video_id("https://youtu.be/dQw4w9WgXcQ?t=3") == "dQw4w9WgXcQ" and tools.video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ" and tools.video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ")
    check("playlist id parsing", tools.playlist_id("https://www.youtube.com/playlist?list=PLabc123456789") == "PLabc123456789")
    check("channel ref parsing", tools.channel_ref("https://www.youtube.com/@Kryptic/videos") == ("handle", "Kryptic") and tools.channel_ref("UCtest000000000000000001")[0] == "id")
    check("compact numbers", metrics.compact(1234) == "1.2K" and metrics.compact(1250000) == "1.3M" and metrics.compact(999) == "999")
    vids_n = [api_youtube.normalize_video(v) for v in STATE["videos"].values()]
    sched = metrics.scheduled(vids_n)
    check("scheduled rule (private + future publishAt)", len(sched) == 9 and sched[0]["video_id"] == "sch00000000")
    cal = metrics.calendar({"UCtest000000000000000001": ("Test", vids_n)}, days=400)
    check("calendar stock ✅ over 7", cal["channels"][0]["ok"] is True and cal["channels"][0]["missing_description"] == 1)
    check("calendar day buckets", sum(len(v) for v in cal["days"].values()) == 9)
    out = metrics.outlier_scores(vids_n)
    check("outlier score flags the 90k video", out.get("vid00000025") and out["vid00000025"] > 10, str(out.get("vid00000025")))
    check("outlier needs baseline (oldest uploads score None)", out.get("vid00000001") is None and out.get("vid00000036") is not None, str((out.get("vid00000001"), out.get("vid00000036"))))
    rk = metrics.latest_ranking(vids_n, n=5)
    check("latest ranking ranks newest 5", len(rk) == 5 and rk[0]["rank"] == 1 and all(r["arrow"] in "↑↓→" for r in rk))
    check("milestones crossed", metrics.milestones_crossed(9500, 10500) == [10000] and metrics.milestones_crossed(None, 5) == [])
    er = metrics.estimated_revenue([{"views": 100000, "is_short": 0}, {"views": 50000, "is_short": 1}])
    check("estimated revenue longform 2-6/1k",
          er["long"]["low"] == 200.0 and er["long"]["high"] == 600.0 and er["long"]["views"] == 100000)
    check("estimated revenue shorts .20-.40/1k",
          er["short"]["low"] == 10.0 and er["short"]["high"] == 20.0)
    check("estimated revenue combined range",
          er["combined"]["low"] == 210.0 and er["combined"]["high"] == 620.0)
    # ------------------------------------------------- public window stats (28d/1yr/lifetime)
    import sf.search as search_mod
    from datetime import datetime as _dt, timezone as _tz
    _now = _dt(2026, 9, 9, tzinfo=_tz.utc)

    def _pv(vid, iso, views, is_short=0, likes=0, comments=0, dur=600, privacy="public"):
        return {"video_id": vid, "published_at": iso, "views": views, "is_short": is_short,
                "likes": likes, "comments": comments, "duration_s": dur, "privacy": privacy}
    _pA = _pv("a", "2026-09-04T00:00:00Z", 10000, 0, 500, 100, 600)          # 5 days ago
    _pB = _pv("b", "2026-07-31T00:00:00Z", 5000, 0, 100, 20, 600)            # 40 days ago
    _pC = _pv("c", "2026-02-21T00:00:00Z", 20000, 1, 1000, 50, 30)           # ~200 days ago, short
    _pP = _pv("p", "2026-09-05T00:00:00Z", 999, 0, 9, 9, 600, "private")     # excluded
    _all = [_pA, _pB, _pC, _pP]
    pw28 = metrics.public_window_stats(_all, days=28, now=_now)
    # uploads = videos POSTED in the window (1); sample_views = their view sum (10000); but the
    # headline "views" is now the channel-wide 28d estimate (residual decay incl. older uploads).
    check("public 28d: one upload posted in window, excludes older + private",
          pw28["uploads"] == 1 and pw28["uploads_long"] == 1 and pw28["sample_views"] == 10000)
    check("public 28d views = channel-wide estimate incl. residual on old uploads",
          pw28["views_source"] == "estimated" and pw28["views"] >= 10000)
    check("public 28d engagement rate", pw28["engagements"] == 600 and round(pw28["engagement_rate"], 3) == 0.06)
    check("public 28d watch-hours estimate", pw28["watch_hours"] == 833)
    pw365 = metrics.public_window_stats(_all, days=365, now=_now)
    check("public 1yr: 2 long + 1 short = 35k views", pw365["uploads"] == 3 and pw365["uploads_long"] == 2 and pw365["uploads_short"] == 1 and pw365["views"] == 35000)
    check("public 1yr revenue split by format", pw365["est_revenue"]["long"]["views"] == 15000 and pw365["est_revenue"]["short"]["views"] == 20000)
    pwl = metrics.public_window_stats(_all, days=None, now=_now, channel_stats={"views": 1000000, "videos": 42})
    check("public lifetime uses channel total views", pwl["views"] == 1000000 and pwl["total_videos"] == 42 and pwl["lifetime"] is True)
    check("public lifetime scales revenue to total views", pwl["est_revenue"]["combined"]["low"] > 900 and pwl["est_revenue"]["long"]["views"] > pw365["est_revenue"]["long"]["views"])
    _rows = [{"day": "2026-08-01", "subscribers": 100, "views": 1000, "videos": 10},
             {"day": "2026-09-01", "subscribers": 150, "views": 1600, "videos": 12}]
    d30 = search_mod._delta(_rows, 30)
    check("snapshot delta 30d", d30 and d30["subscribers"] == 50 and d30["views"] == 600 and d30["days"] == 31)
    check("snapshot delta needs two rows", search_mod._delta(_rows[:1], 30) is None)
    check("snapshot delta whole span", search_mod._delta(_rows, None)["since"] == "2026-08-01")
    # movie / compilation channels earn a higher longform RPM
    _movie = [{"privacy": "public", "is_short": 0, "duration_s": 5400, "views": 500000,
               "published_at": "2026-06-01T00:00:00Z", "video_id": f"mv{i}"} for i in range(6)]
    check("movie channel detected by duration", metrics.is_movie_channel(_movie) is True)
    _mw = metrics.public_window_stats(_movie, days=None)
    check("movie longform RPM is 10-20", _mw["movie_channel"] is True and _mw["est_revenue"]["rpm_long"] == [10.0, 20.0])
    import sf.predict as _predict, sf.thumbs as _thumbs
    check("predict parses video ids", _predict.parse_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
          and _predict.parse_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5") == "dQw4w9WgXcQ"
          and _predict.parse_video_id("garbage") is None)
    check("thumbs styles present", len(_thumbs.STYLES) >= 8 and "mrbeast" in _thumbs.STYLES)
    _tp = _thumbs.build_prompt("mrbeast", "giant cash scene", "$1,000,000", "", [("face", "x")])
    check("thumbs prompt carries text + face legend", "$1,000,000" in _tp and "PERSON to feature" in _tp)
    # residual/evergreen: a channel-wide window view delta drives the window's views + revenue
    _rv = metrics.public_window_stats(_all, days=28, now=_now, window_views=250000)
    check("window_views = residual-inclusive channel-wide views", _rv["views"] == 250000 and _rv["views_source"] == "channel_wide")
    check("no window delta -> residual decay estimate", metrics.public_window_stats(_all, days=28, now=_now)["views_source"] == "estimated")
    # residual decay model, calibrated to two real Studio curves
    from datetime import timedelta as _td
    check("residual: fresh video (age<window) counts its whole total",
          metrics.video_window_views(40000, 5, 28, 8.0) == 40000)
    check("decay shape is a normalised cumulative curve",
          metrics.shape_cum(0) == 0 and 0 < metrics.shape_cum(1) < metrics.shape_cum(28) < metrics.shape_cum(365)
          < metrics.shape_cum(7400) <= 1.0001 and abs(metrics.shape_cum(7400) - 1) < 0.01)
    _sh = lambda d: metrics.shape_cum(d) / metrics.shape_cum(300)
    check("shape: a long-form upload gets 30-75% of its first-year views in month one and >85% by month six (real daily curves)",
          0.3 < _sh(28) < 0.75 and 0.85 < _sh(182) < 0.99)
    _ss = lambda d: metrics.shape_cum(d, is_short=1) / metrics.shape_cum(300, is_short=1)
    check("shape: Shorts burst earlier than long-form", _ss(7) > _sh(7) * 0.9 and 0 < _ss(28) <= 1)
    check("shape: a 15-month-old upload still gets a slice of its views in the last year, a 6-year-old very little",
          0.05 < metrics.video_window_views(1.0, 450, 365) < 0.6 and metrics.video_window_views(1.0, 2100, 365) < 0.05)
    check("shape: a bigger outlier keeps a heavier evergreen tail",
          metrics.video_window_views(1.0, 800, 365, outlier=30) > metrics.video_window_views(1.0, 800, 365, outlier=1) > metrics.video_window_views(1.0, 800, 365, outlier=0.1))
    _ctxA = metrics.catalog_context([{"video_id": "a", "views": 1000, "is_short": 0, "published_at": "2026-01-01T00:00:00Z"}] * 3, {"subscribers": 100000}, _now)
    _ctxB = metrics.catalog_context([{"video_id": "a", "views": 1000, "is_short": 0, "published_at": "2026-01-01T00:00:00Z"}] * 3, {"subscribers": 100}, _now)
    check("context: discovery-driven channels (high views per sub) get a heavier plateau", _ctxB["mp"][0] > _ctxA["mp"][0] and _ctxA["cadence"] == 0.1)
    _ctxC = metrics.catalog_context([{"video_id": f"c{i}", "views": 1000, "is_short": 0, "published_at": (_now - _td(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ")} for i in range(60)], None, _now)
    check("context: frequent uploaders get a lighter evergreen tail", _ctxC["me_ch"] < _ctxA["me_ch"] and _ctxC["cadence"] > 4)
    _l, _t = metrics.video_daily_slices(1000, 100, 91)
    check("daily slices split launch/tail and sum to the window slice",
          abs(sum(_l) + sum(_t) - metrics.video_window_views(1000, 100, 91)) < 1e-6 and sum(_l) > 0 and sum(_t) > 0
          and all(x == 0 for x in _l[:40]))
    _cat = [{"video_id": f"c{i}", "published_at": (_now - _td(days=age)).strftime("%Y-%m-%dT%H:%M:%SZ"), "views": 10000,
             "privacy": "public", "is_short": 0} for i, age in enumerate((5, 40, 100, 400, 1500))]
    _w = metrics.channel_view_windows(_cat, [("28d", 28), ("1yr", 365)], now=_now)
    _ctx = metrics.catalog_context(_cat, None, _now)
    check("channel windows = sum of per-upload slices (model)",
          _w["28d"]["method"] == "estimate" and abs(_w["28d"]["views"] - sum(metrics.video_window_views(10000, a, 28, ctx=_ctx) for a in (5, 40, 100, 400, 1500))) <= 1
          and _w["1yr"]["views"] > _w["28d"]["views"] and _w["28d"]["sample"] == 5)
    _rows = [{"day": (_now - _td(days=d)).strftime("%Y-%m-%d"), "views": 1000000 - d * 10000} for d in (0, 1, 2, 3, 5, 8)]
    _m = metrics.measured_from_snapshots(_rows, [("28d", 28), ("7d", 7)])
    check("snapshots -> measured span + exact per-window deltas (interpolated between snapshot days)",
          _m["days"] == 8 and _m["views"] == 80000 and _m["per_window"] == {7: 70000.0} and not _m["trimmed"])
    _lumpy = metrics.measured_from_snapshots(_rows[:-1] + [{"day": _rows[-1]["day"], "views": _rows[-1]["views"] - 300000}], [("7d", 7)])
    check("a lumpy channel-total update (one 300K day) is trimmed out of the calibration rate",
          _lumpy["trimmed"] and _lumpy["raw_views"] == 380000 and 60000 <= _lumpy["views"] <= 90000)
    _wb = metrics.channel_view_windows(_cat, [("7d", 7), ("28d", 28)], now=_now, measured=_m)
    check("a window the snapshots cover is measured exactly", _wb["7d"]["method"] == "measured" and _wb["7d"]["views"] == 70000)
    # dormant channels (no upload for 180+ days): fitted on K9's Studio export, see sf/metrics.py
    _iso = lambda age: (_now - _td(days=age)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _dcat = [{"video_id": f"d{i}", "published_at": _iso(age), "views": 100000, "privacy": "public", "is_short": sh}
             for i, (age, sh) in enumerate([(400, 0), (600, 0), (800, 0), (500, 1), (700, 1)])]
    _dctx = metrics.catalog_context(_dcat, None, _now)
    _actx = metrics.catalog_context(_dcat + [{"video_id": "fresh", "published_at": _iso(10), "views": 100000,
                                              "privacy": "public", "is_short": 0}], None, _now)
    check("a channel with no upload for 180+ days is dormant, one that uploaded 10 days ago is not",
          _dctx["dormant"] and _dctx["days_since_upload"] == 400 and not _actx["dormant"])
    _dl, _al = (metrics.video_window_views(100000, 800, 28, is_short=0, ctx=x) for x in (_dctx, _actx))
    _ds, _as = (metrics.video_window_views(100000, 700, 28, is_short=1, ctx=x) for x in (_dctx, _actx))
    check("dormant: old long-form keeps more of its views, old Shorts far fewer (K9 export)", _dl > _al and _ds < _as * 0.5)
    check("dormant windows report the regime", metrics.channel_view_windows(_dcat, [("28d", 28)], now=_now)["28d"]["context"]["dormant"])
    # "only N scheduled" needs a scan that can SEE scheduled uploads (the channel's own token)
    _sch = {"channel_id": "UCstock", "title": "Stock"}
    _sv = [{"video_id": "sv1", "privacy": "public", "views": 5}]
    _a_pub, _ = alerts.scan_channel(store, _sch, _sv, {"videos": {}}, sees_private=False)
    _a_tok, _ = alerts.scan_channel(store, _sch, _sv, {"videos": {}}, sees_private=True)
    check("'only N scheduled' never fires on public data, still fires through the channel's token",
          not any(a["kind"] == "low_stock" for a in _a_pub) and any(a["kind"] == "low_stock" for a in _a_tok))
    store.add_alert({"key": "stock:UCstock:2026-09-09", "kind": "low_stock", "channel_id": "UCstock", "title": "Stock: only 0 scheduled"})
    check("start-up cleanup dismisses the blind 'only 0 scheduled' alerts",
          alerts.retire_blind_stock_alerts(store) == 1 and not any(a["kind"] == "low_stock" for a in store.alerts()))
    # measurement wins: when the counter says the old uploads are dead, the window follows it (Chica:
    # the model said 3.3M for 28 days, the counter 741K)
    _dead = metrics.channel_view_windows(_cat, [("28d", 28)], now=_now, measured=dict(_m, raw_views=2000, views=2000, per_window={}))
    check("measurement beats the model: a near-dead tail pulls the window far under the model",
          _dead["28d"]["method"] == "partial" and _dead["28d"]["views"] < 0.5 * _dead["28d"]["model"] and _dead["28d"]["tail_k"] <= 0.1)
    check("a partly measured window is the measured days + the corrected model, exact in (window - days)",
          _wb["28d"]["method"] == "partial" and _wb["28d"]["tail_k"] is not None and _wb["28d"]["views"] >= 80000
          and _wb["28d"]["exact_in_days"] == 20 and _wb["28d"]["typical_error_pct"] == 14)
    _wr = metrics.channel_view_windows(_cat, [("28d", 28), ("3mo", 91)], now=_now, measured=_m, retention_days=30)
    check("a window longer than the snapshots may be kept stays an estimate for good (YouTube's 30-day rule)",
          _wr["3mo"]["method"] == "estimate" and _wr["3mo"]["exact_in_days"] is None and _wr["3mo"]["beyond_retention"]
          and _wr["28d"]["method"] == "partial" and _wr["28d"]["exact_in_days"] == 20)
    _w2 = metrics.channel_view_windows(_cat, [("28d", 28)], now=_now, measured=dict(_m, days=2, raw_views=20000, per_window={}))
    check("under 3 days of readings: the pure model, labelled an estimate", _w2["28d"]["method"] == "estimate" and _w2["28d"]["tail_k"] is None)
    check("the 1-year window lets the correction fade (bursts live in the uploads' ages), 3/6 months keep it",
          metrics.tail_memory(365) == 60 and metrics.tail_memory(91) == float("inf") and metrics.tail_memory(182) == float("inf"))
    # readings carry the moment they were taken: windows are cut at those moments
    _t0 = _now.timestamp()
    _ts = [{"day": (_now - _td(days=d)).strftime("%Y-%m-%d"), "views": 1000000 - 10000 * d, "fetched_at": _t0 - d * 86400 - (0 if d == 0 else 6 * 3600)} for d in (0, 1, 2, 3, 4, 5, 6, 7, 8)]
    _mt = metrics.measured_from_snapshots(_ts, [("7d", 7)])
    # the 7-day start falls 6 h after the reading taken 7 days + 6 h ago: 930,000 + 0.25 x 10,000
    # (day labels alone would say 70,000)
    check("snapshot times (not just days) place the window start", abs(_mt["per_window"][7] - 67500) < 1)
    _gap = metrics.measured_from_snapshots([{"day": (_now - _td(days=d)).strftime("%Y-%m-%d"), "views": 1000000 - 10000 * d} for d in (0, 1, 10, 11, 12)], [("7d", 7)])
    _wg = metrics.channel_view_windows(_cat, [("7d", 7)], now=_now, measured=_gap)
    check("a window whose start falls in a gap between readings is marked interpolated",
          _wg["7d"]["method"] == "measured" and _wg["7d"]["interpolated"] and _wg["7d"]["gap_days"] == 9.0)
    _wn = metrics.channel_view_windows(_cat, [("7d", 7)], now=_now, measured=metrics.measured_from_snapshots(
        [{"day": (_now - _td(days=d)).strftime("%Y-%m-%d"), "views": 1000000 - 10000 * d} for d in range(9)], [("7d", 7)]))
    check("...and one read every day is exact", _wn["7d"]["method"] == "measured" and not _wn["7d"]["interpolated"])
    _cwv = metrics.channel_window_views(_cat, 28, now=_now, total_videos=40, lifetime_views=10_000_000, channel_age_days=3000)
    check("channel_window_views returns parts", "views" in _cwv and _cwv["views"] > 0 and "boost" in _cwv and _cwv["back_catalog"] > 0)
    _vrows = [{"video_id": "c2", "day": (_now - _td(days=d)).strftime("%Y-%m-%d"), "views": 9000 + d * 10} for d in (0, 4, 8)] \
        + [{"video_id": "c3", "day": _now.strftime("%Y-%m-%d"), "views": 5}] \
        + [{"video_id": "c4", "day": (_now - _td(days=3)).strftime("%Y-%m-%d"), "views": 5}, {"video_id": "c4", "day": (_now - _td(days=9)).strftime("%Y-%m-%d"), "views": 1}]
    _mv = metrics.measured_videos_from_snapshots(_vrows, _now.strftime("%Y-%m-%d"))
    check("per-upload snapshots -> deltas only for uploads seen on the latest day with history",
          set(_mv) == {"c2"} and _mv["c2"]["days"] == 8 and _mv["c2"]["views"] == 0)
    _wv = metrics.channel_view_windows(_cat, [("28d", 28)], now=_now, measured=dict(_m, videos={"c2": {"days": 8, "views": 600}}))
    check("the channel counter is the measurement (per-upload deltas do not move the window)",
          _wv["28d"]["views"] == _wb["28d"]["views"])
    search_mod.record_video_snapshots(store, "UCsnaptest", [{"video_id": "s1", "views": 100}, {"video_id": "s2", "views": 5}, {"video_id": "s3"}])
    check("per-upload snapshots recorded on lookup/sync (one row per upload per day)",
          store._one("SELECT COUNT(*) AS n FROM video_snapshots WHERE channel_id='UCsnaptest'")["n"] == 2 and search_mod.video_snapshot_deltas(store, "UCsnaptest") == {})
    _older = (__import__("datetime").datetime.strptime(search_mod._today(), "%Y-%m-%d") - _td(days=10)).strftime("%Y-%m-%d")
    store._exec("INSERT OR REPLACE INTO video_snapshots VALUES ('UCsnaptest','s1',?,40)", (_older,))
    _sd = search_mod.video_snapshot_deltas(store, "UCsnaptest")
    check("per-upload deltas come back once an older day exists", _sd.get("s1", {}).get("days") == 10 and _sd["s1"]["views"] == 60 and "s2" not in _sd)
    combined = metrics.combine_daily({"a": [{"day": "2026-09-01", "views": 10, "engaged_views": 8}], "b": [{"day": "2026-09-01", "views": 5, "engaged_views": 4}, {"day": "2026-09-02", "views": 1}]})
    check("combine daily across channels", combined[0]["views"] == 15 and combined[0]["engaged_views"] == 12 and combined[1]["channels"] == 1)
    tot = metrics.sum_totals(combined)
    check("sum totals + engaged ratio", tot["views"] == 16 and tot["engaged_ratio"] == 0.75)
    check("scopes_for dedupes", len(oauth.scopes_for(["readonly", "readonly", "analytics"])) == 2)
    url, st = oauth.auth_url(cfg, "http://127.0.0.1:5999/oauth/cb", None, auth_base=f"{base}/auth")
    check("auth url carries offline+consent+scopes", "access_type=offline" in url and "prompt=consent" in url and "yt-analytics" in url and st in url)
    hits = innertube.keys_matching({"a": {"realtimeViews": 12, "deep": {"last48Hours": "1.2K", "other": 1}}, "list": [{"engagedViewCount": 5}]}, r"realtime|last48|engaged")
    check("probe key walker", {h["key"] for h in hits} == {"realtimeViews", "last48Hours", "engagedViewCount"})
    check("find/replace preview", tools.find_replace_preview([{"video_id": "x", "title": "Hello World", "description": "world"}], "world", "there")[0]["changes"] == {"title": "Hello there", "description": "there"})
    check("find/replace case sensitive", tools.find_replace_preview([{"video_id": "x", "title": "Hello World"}], "world", "t", case_sensitive=True) == [])

    # --------------------------------------------------- accounts + isolation
    r = c.get("/api/status").get_json()
    check("status public before login", r["authed"] is False and "ext_token" not in r and r["user"] is None)
    check("auth providers advertised", r["auth"]["password"] and r["auth"]["google"] and r["auth"]["discord"])
    check("api gated", c.get("/api/channels").status_code == 401)
    check("signup rejects short password", c.post("/api/auth/signup",
          json={"email": "erik@x.com", "password": "short"}).status_code == 400)
    r = c.post("/api/auth/signup", json={"email": "erik@x.com", "password": "hunter2secret", "name": "Erik"}).get_json()
    check("first signup claims owner (uid 1)", r["ok"] and r["user"]["id"] == 1 and r["user"]["email"] == "erik@x.com")
    r = c.get("/api/status").get_json()
    check("status after signup", r["authed"] and r["ext_token"] == "ext-secret" and r["channels"] == 0 and r["user"]["id"] == 1)
    check("duplicate email refused", c.post("/api/auth/signup",
          json={"email": "erik@x.com", "password": "whatever12"}).status_code == 409)
    check("index served", b"CreatorHaven" in c.get("/").data)
    check("logout clears session", c.post("/api/auth/logout").get_json()["ok"] and c.get("/api/status").get_json()["authed"] is False)
    check("wrong password rejected", c.post("/api/auth/login",
          json={"email": "erik@x.com", "password": "nope"}).status_code == 403)
    check("login ok", c.post("/api/auth/login", json={"email": "erik@x.com", "password": "hunter2secret"}).get_json()["ok"])
    check("me returns the account", c.get("/api/auth/me").get_json()["user"]["id"] == 1)

    # ------------------------------------------------------------- linking
    r = c.get("/api/oauth/start?sets=readonly,analytics").get_json()
    check("oauth start returns url", r["url"].startswith(f"{base}/auth") and r["redirect_uri"].endswith("/oauth/cb"))
    state_ = urllib.parse.parse_qs(urllib.parse.urlparse(r["url"]).query)["state"][0]
    check("callback state mismatch rejected", c.get("/oauth/cb?code=good-code&state=bad").status_code == 400)
    r = c.get(f"/oauth/cb?code=good-code&state={state_}")
    check("callback links the channel", r.status_code == 302 and "linked=Test+Channel" in r.headers["Location"].replace("%20", "+"))
    ch = store.channel("UCtest000000000000000001")
    check("channel persisted w/ token + scopes", ch and ch["refresh_token"] == "rt-1" and "yt-analytics" in ch["scopes"])
    check("bad code -> redeem error", c.post("/api/oauth/redeem", json={"code": "bad"}).status_code == 400)

    # import from schedule bot (fake dir)
    bot = tmp / "bot"
    (bot / "data" / "tokens").mkdir(parents=True)
    (bot / "data" / "tokens" / "x.json").write_text(json.dumps({"channelId": "UCimported0000000000000001", "title": "Imported Channel", "uploadsPlaylistId": "UUimp", "refreshToken": "rt-imported"}))
    imported = sync.import_schedule_bot(store, cfg, bot_dir=bot)
    check("import from schedule bot", imported == ["Imported Channel"] and store.channel("UCimported0000000000000001")["scopes"] == oauth.SCOPE_READONLY)
    check("import is idempotent", sync.import_schedule_bot(store, cfg, bot_dir=bot) == [])

    # ---------------------------------------------------------------- sync
    r = c.post("/api/sync/now", json={}).get_json()["result"]
    by = {x["channel_id"]: x for x in r}
    t = by["UCtest000000000000000001"]
    check("sync pulls videos", t["videos"] == 49, str(t))
    check("sync pulls a year of analytics daily (exact 6-month / 1-year windows on the Search page)", t["daily"] == 365 and t["analytics"] == "ok", str(t["errors"]))
    check("imported channel lacks analytics scope", by["UCimported0000000000000001"]["analytics"] == "needs_scope" and by["UCimported0000000000000001"]["videos"] == 49)
    d = store.daily("UCtest000000000000000001", "2000-01-01", "2999-01-01")
    check("daily rows carry engaged views", len(d) == 365 and d[0]["engaged_views"] == 800 and d[0]["revenue"] == 12.5)
    check("first scan is baseline (no alert flood)", t["alerts"] == 0 or all(a["kind"] == "scheduled_no_description" for a in store.alerts()), str([a["kind"] for a in store.alerts()]))
    check("scheduled-without-description alert", any(a["kind"] == "scheduled_no_description" for a in store.alerts()))
    q = store.quota(sync.today())
    check("quota accounted", q["units"] >= 4 and q["calls"] >= 4, str(q))
    stats = store.channel("UCtest000000000000000001")
    check("channel stats stored", json.loads(stats["stats_json"])["subscribers"] == 125000)

    # second sync: mutate state -> alerts
    STATE["videos"]["vid00000005"]["contentDetails"]["contentRating"] = {"ytRating": "ytAgeRestricted"}
    STATE["videos"]["vid00000006"]["status"]["madeForKids"] = True
    STATE["videos"]["vid00000007"]["status"]["privacyStatus"] = "private"
    STATE["videos"]["vid00000008"]["statistics"]["viewCount"] = "1200000"
    r = c.post("/api/sync/now", json={}).get_json()["result"]
    kinds = {a["kind"] for a in store.alerts()}
    check("alerts: age restriction", "age_restricted" in kinds, str(kinds))
    check("alerts: made for kids", "made_for_kids" in kinds)
    check("alerts: privacy changed", "privacy_changed" in kinds)
    check("alerts: view milestone", "view_milestone" in kinds)
    n_before = len(store.alerts())
    c.post("/api/sync/now", json={})
    check("alerts dedupe on re-scan", len(store.alerts()) == n_before)
    check("discord delivery posted", STATE.get("discord", {}).get("content", "").count("\n") >= 1 and all(a["delivered"] for a in store.alerts()))
    aid = store.alerts()[0]["id"]
    c.post(f"/api/alerts/{aid}/dismiss")
    check("dismiss alert", all(a["id"] != aid for a in store.alerts()))

    # analytics error classification
    STATE["analytics_403"] = ("accessNotConfigured", "YouTube Analytics API has not been used in project 123 before or it is disabled.")
    r = c.post("/api/sync/now", json={"channel_id": "UCtest000000000000000001"}).get_json()["result"][0]
    check("analytics api-disabled detected", r["analytics"] == "api_disabled", str(r))
    STATE["analytics_403"] = ("insufficientPermissions", "Request had insufficient authentication scopes.")
    r = c.post("/api/sync/now", json={"channel_id": "UCtest000000000000000001"}).get_json()["result"][0]
    check("analytics needs-scope detected", r["analytics"] == "needs_scope")
    STATE["analytics_403"] = None
    STATE["engaged_400"] = True
    r = c.post("/api/sync/now", json={"channel_id": "UCtest000000000000000001"}).get_json()["result"][0]
    check("engagedViews 400 falls back to views", r["analytics"] == "ok" and r["daily"] == 365, str(r["errors"]))
    STATE["engaged_400"] = False

    # ------------------------------------------ multi-user isolation + OAuth sign-in
    PNG1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMCAoHz5v0AAAAASUVORK5CYII="
    # owner (client c) saves a thumbnail reference + a strategy note
    check("owner saves a thumbnail ref", c.post("/api/thumbs/ref",
          json={"kind": "face", "image_b64": PNG1, "mime": "image/png"}).get_json()["ok"])
    owner_refs = c.get("/api/thumbs").get_json()["refs"]["face"]
    check("owner sees own ref", len(owner_refs) == 1)
    # a second creator signs up on a fresh session -> a brand-new empty account
    c2 = application.test_client()
    r = c2.post("/api/auth/signup", json={"email": "bob@x.com", "password": "bobpass1234", "name": "Bob"}).get_json()
    check("second signup gets a new account (uid 2)", r["ok"] and r["user"]["id"] == 2)
    check("new user has zero channels", c2.get("/api/channels").get_json()["channels"] == [])
    check("new user overview is empty", c2.get("/api/overview").get_json()["channels"] == [])
    check("new user cannot read owner channel", c2.get("/api/channels/UCtest000000000000000001").status_code == 404)
    check("new user cannot sync owner channel", c2.post("/api/sync/now", json={"channel_id": "UCtest000000000000000001"}).status_code == 404)
    check("new user cannot delete owner channel", c2.delete("/api/channels/UCtest000000000000000001").status_code == 404)
    check("owner channel survives the attempts", store.channel("UCtest000000000000000001") is not None)
    check("new user sees none of the owner's thumbnails", c2.get("/api/thumbs").get_json()["refs"]["face"] == [])
    c2.post("/api/thumbs/ref", json={"kind": "face", "image_b64": PNG1, "mime": "image/png"})
    check("owner still sees only their own ref", len(c.get("/api/thumbs").get_json()["refs"]["face"]) == 1)
    # sign in WITH Google -> yet another distinct account
    cg = application.test_client()
    loc = cg.get("/auth/google").headers["Location"]
    gstate = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["state"][0]
    r = cg.get(f"/auth/google/cb?code=good&state={gstate}")
    check("google sign-in redirects home", r.status_code == 302 and r.headers["Location"].endswith("/"))
    gu = cg.get("/api/auth/me").get_json()["user"]
    check("google account created + isolated", gu and gu["email"] == "guser@gmail.com" and gu["id"] == 3)
    check("google user has zero channels", cg.get("/api/channels").get_json()["channels"] == [])
    # sign in WITH Discord
    cd = application.test_client()
    loc = cd.get("/auth/discord").headers["Location"]
    dstate = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["state"][0]
    cd.get(f"/auth/discord/cb?code=good&state={dstate}")
    du = cd.get("/api/auth/me").get_json()["user"]
    check("discord account created + isolated", du and du["email"] == "erikd@discord.test" and du["id"] == 4)
    check("users database tracks every signup", store.user_count() == 4)
    STATE["discord_unverified"] = True
    cx = application.test_client()
    loc = cx.get("/auth/discord").headers["Location"]
    xstate = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["state"][0]
    r = cx.get(f"/auth/discord/cb?code=good&state={xstate}")
    STATE["discord_unverified"] = False
    check("an unverified sign-in address cannot open the account with that email",
          "auth_error" in r.headers.get("Location", "") and cx.get("/api/auth/me").get_json()["user"] is None
          and not store.user_by_provider("discord", "d-888") and store.user_count() == 4)
    check("owner-only settings guard blocks non-owner", c2.post("/api/settings", json={"sync_minutes": 5}).status_code == 403)
    # re-auth the owner client for the remaining tests (its session is unchanged, but assert it)
    check("owner session intact", c.get("/api/auth/me").get_json()["user"]["id"] == 1)
    import base64 as _b64   # main() imports base64 locally further down, so the global name is shadowed here
    check("members never receive the owner's extension token (it acts as the owner)", c2.get("/api/status").get_json().get("ext_token") not in (None, "ext-secret"))
    # dismiss all: one click, scoped to the signed-in account
    store.add_alert({"key": "bob-alert-1", "kind": "view_milestone", "title": "Bob's video passed 1K"}, user_id=2)
    bob_aid = [a for a in store.alerts(user_id=2)][0]["id"]
    c.post(f"/api/alerts/{bob_aid}/dismiss")
    check("an alert id from another account cannot be dismissed", any(a["id"] == bob_aid for a in store.alerts(user_id=2)))
    n_open = len(store.alerts(user_id=1))
    r = c.post("/api/alerts/dismiss-all", json={}).get_json()
    check("dismiss all clears every open alert of the account", r["ok"] and r["dismissed"] == n_open and store.alerts(user_id=1) == [])
    check("dismiss all leaves other accounts' alerts alone", len(store.alerts(user_id=2)) == 1)
    check("dismissed alerts stay viewable", len(c.get("/api/alerts?all=1").get_json()["alerts"]) >= n_open)
    # public thumbnail generator: a daily free allowance per account, uploads checked + cleaned + capped
    import io as _io
    from sf import imagegen as _ig
    _orig_gen = _ig.generate_image
    _ig.generate_image = lambda key, prompt, refs=None, aspect_ratio="16:9": _b64.b64decode(PNG1)
    try:
        check("owner sets the daily free allowance",
              c.post("/api/admin/whitelist", json={"thumbs_daily_free": 1}).get_json().get("thumbs_daily_free") == 1)
        check("member cannot change the allowance", c2.post("/api/admin/whitelist", json={"thumbs_daily_free": 99}).status_code == 403)
        check("member sees 1 free thumbnail left", c2.get("/api/thumbs").get_json()["allowance"] == {"limit": 1, "used": 0, "left": 1})
        r = c2.post("/api/thumbs/generate", json={"subject": "a cat"})
        check("a member's free generation works and is counted",
              r.status_code == 200 and r.get_json()["ok"] and r.get_json()["allowance"]["left"] == 0)
        r = c2.post("/api/thumbs/generate", json={"subject": "a cat"})
        check("a member over the allowance is refused (429)", r.status_code == 429 and "free thumbnails" in r.get_json()["error"])
        check("the owner is unlimited", c.get("/api/thumbs").get_json()["allowance"]["limit"] is None
              and c.post("/api/thumbs/generate", json={"subject": "x"}).get_json()["ok"])
        c.post("/api/admin/whitelist", json={"thumbs_daily_free": 0})
        r = c2.post("/api/thumbs/generate", json={"subject": "a cat"})
        check("allowance 0 = whitelist only", r.status_code == 429 and "whitelisted" in r.get_json()["error"])
    finally:
        _ig.generate_image = _orig_gen
    r = c2.post("/api/thumbs/ref", json={"kind": "style", "image_b64": _b64.b64encode(b"not an image at all").decode(), "mime": "image/png"})
    check("a non-image upload is refused", r.status_code == 400 and not r.get_json()["ok"])
    try:
        from PIL import Image as _Img
        _buf = _io.BytesIO()
        _ex = _Img.Exif(); _ex[270] = "secret-gps-note"
        _Img.new("RGB", (40, 30), (200, 10, 10)).save(_buf, "JPEG", exif=_ex.tobytes())
        assert b"secret-gps-note" in _buf.getvalue()
        r = c2.post("/api/thumbs/ref", json={"kind": "style", "image_b64": _b64.b64encode(_buf.getvalue()).decode(), "mime": "image/jpeg"}).get_json()
        _saved = (sfconfig.DATA / "thumbs" / "u2" / "refs" / "style" / f"{r['ref']['id']}.{r['ref']['ext']}").read_bytes()
        check("uploads are re-encoded without their EXIF metadata", r["ok"] and b"secret-gps-note" not in _saved)
    except ImportError:
        pass
    for _i in range(12):
        c2.post("/api/thumbs/ref", json={"kind": "brand", "image_b64": PNG1, "mime": "image/png"})
    r = c2.post("/api/thumbs/ref", json={"kind": "brand", "image_b64": PNG1, "mime": "image/png"})
    check("12 references per kind, then a clear refusal", r.status_code == 400 and "12" in r.get_json()["error"]
          and len(c2.get("/api/thumbs").get_json()["refs"]["brand"]) == 12)
    # a brand-new account never inherits a leftover thumbnail folder that happens to carry its id
    _left = sfconfig.DATA / "thumbs" / "u5" / "refs" / "face"
    _left.mkdir(parents=True, exist_ok=True)
    (_left / "0123456789abcdef.png").write_bytes(_b64.b64decode(PNG1))
    c5 = application.test_client()
    r = c5.post("/api/auth/signup", json={"email": "eve@x.com", "password": "evepass1234", "name": "Eve"}).get_json()
    check("a new account starts with empty references even if a u<id> folder was left behind",
          r["user"]["id"] == 5 and c5.get("/api/thumbs").get_json()["refs"]["face"] == []
          and any((sfconfig.DATA / "thumbs" / "_retired").glob("u5-*")))
    # ---- public release: personal extension keys, owner-only endpoints, lookup budget, privacy, deletion
    bob_key = c2.get("/api/status").get_json().get("ext_token")
    check("every account gets its own extension key (not the owner's)", bool(bob_key) and bob_key != "ext-secret")
    check("a personal key connects the extension to THAT account",
          c.get("/api/ext/ping", headers={"X-SF-Token": bob_key}).get_json().get("channels") == 0)
    check("an unknown key is refused", c.get("/api/ext/ping", headers={"X-SF-Token": "nope"}).status_code == 401)
    check("owner-only extension endpoints refuse a member key",
          c.post("/api/ext/probe", json={"entries": []}, headers={"X-SF-Token": bob_key}).status_code == 403
          and c.post("/api/ext/probe", json={"entries": []}, headers={"X-SF-Token": "ext-secret"}).status_code == 200)
    _units_before = cfg.get("ext_daily_units")
    cfg["ext_daily_units"] = 3
    r = c.post("/api/ext/baselines", json={"channels": ["@nobodyone", "@nobodytwo"]}, headers={"X-SF-Token": bob_key}).get_json()["baselines"]
    check("a member's uncached lookups stop at the daily budget (cached answers stay free)",
          r.get("@nobodytwo") == {"limited": True} and r.get("@nobodyone") != {"limited": True}, str(r))
    r = c.post("/api/ext/baselines", json={"channels": ["@nobodyone"]}, headers={"X-SF-Token": bob_key}).get_json()["baselines"]
    check("an answer cached by the first lookup costs nothing", r.get("@nobodyone") != {"limited": True}, str(r))
    r = c.post("/api/ext/baselines", json={"channels": ["@nobodythree"]}, headers={"X-SF-Token": "ext-secret"}).get_json()["baselines"]
    check("the owner's extension is not budgeted", r.get("@nobodythree") != {"limited": True})
    cfg["ext_daily_units"] = _units_before
    new_key = c2.post("/api/ext-token/rotate", json={}).get_json().get("ext_token")
    check("a new key replaces the old one", bool(new_key) and new_key != bob_key
          and c.get("/api/ext/ping", headers={"X-SF-Token": bob_key}).status_code == 401
          and c.get("/api/ext/ping", headers={"X-SF-Token": new_key}).status_code == 200)
    anon = application.test_client()
    r = anon.get("/privacy")
    check("the privacy policy is public", r.status_code == 200 and b"privacy policy" in r.data and b"never sent" in r.data)
    cfg["owner_email"], cfg["contact_email"] = "bob@x.com", "hello@creatorhaven.test"
    bob_status = c2.get("/api/status").get_json()
    r = anon.get("/privacy")
    cfg["owner_email"], cfg["contact_email"] = "", ""
    check("an account whose email equals OWNER_EMAIL is not an owner (only account 1 is)",
          bob_status["is_owner"] is False and bob_status["role"] != "owner")
    check("/privacy shows CONTACT_EMAIL and never the owner's address",
          b"hello@creatorhaven.test" in r.data and b"bob@x.com" not in r.data)
    check("the owner can't delete the owner account", c.delete("/api/account", json={"confirm": "DELETE"}).status_code == 400)
    check("deleting needs the typed confirmation", c5.delete("/api/account", json={"confirm": "yes"}).status_code == 400)
    eve_key = c5.get("/api/status").get_json().get("ext_token")
    c5.post("/api/thumbs/ref", json={"kind": "face", "image_b64": PNG1, "mime": "image/png"})
    r = c5.delete("/api/account", json={"confirm": "DELETE"}).get_json()
    check("delete my account removes the account, its key and its thumbnails",
          r.get("ok") and store.get_user(5) is None and c5.get("/api/status").get_json()["authed"] is False
          and c.get("/api/ext/ping", headers={"X-SF-Token": eve_key}).status_code == 401
          and not (sfconfig.DATA / "thumbs" / "u5").exists())
    c.post("/api/sync/now", json={"channel_id": "UCtest000000000000000001"})

    # ------------------------------------------------------------- reads
    ov = c.get("/api/overview?days=28").get_json()
    check("overview combined + series", ov["combined"]["subscribers"] == 250000 and len(ov["series"]) == 28 and ov["totals"]["engaged_views"] > 0)
    det = c.get("/api/channels/UCtest000000000000000001?days=7").get_json()
    check("channel detail", len(det["series"]) == 7 and det["totals"]["views"] > 0 and len(det["top"]) == 10 and det["scopes"]["analytics"])
    check("detail videos carry outlier + tags", any(v.get("outlier") for v in det["videos"]) and det["videos"][0]["tags"] == ["minecraft", "horror"])
    check("detail latest ranking", len(det["latest"]) == 10)
    cal = c.get("/api/calendar?days=400").get_json()
    # the mock serves one catalogue to both channels, so the 9 scheduled rows belong to the last-synced channel
    check("calendar endpoint", len(cal["channels"]) == 2 and sum(len(v) for v in cal["days"].values()) == 9, str(sum(len(v) for v in cal["days"].values())))

    # ------------------------------------------------------------- tools
    def tool(name, **body):
        return c.post(f"/api/tools/{name}", json=body).get_json()
    r = tool("thumbnails", video="https://youtu.be/vid00000001")
    check("thumbnails tool checks sizes", r["ok"] and r["result"]["sizes"][0]["exists"] is False and r["result"]["sizes"][1]["exists"] is True)
    r = tool("channel-id", channel="@testchannel")
    check("channel id tool", r["ok"] and r["result"]["channel_id"] == "UCtest000000000000000001" and r["result"]["rss"].endswith("UCtest000000000000000001"))
    r = tool("channel-id", channel="@nobody")
    check("channel id not found -> 400", not r["ok"] and "not found" in r["error"].lower())
    r = tool("channel-images", channel="UCtest000000000000000001")
    check("channel images upscale", r["ok"] and "=s2048" in r["result"]["profile"]["max"] and r["result"]["banner"]["desktop"].startswith("https://yt3.googleusercontent.com/banner=w2560"))
    r = tool("comment-export", video="vid00000001")
    check("comment export csv", r["ok"] and r["result"]["count"] == 12 and r["result"]["csv"].startswith("comment_id,author"))
    r = tool("playlist-export", playlist="PLabc123456789")
    check("playlist export", r["ok"] and r["result"]["count"] == 49 and "url" in r["result"]["rows"][0])
    r = tool("channel-backup", channel="UCtest000000000000000001")
    check("channel backup", r["ok"] and r["result"]["count"] == 49 and "tags" in r["result"]["csv"].splitlines()[0])
    r = tool("video-analyzer", video="vid00000002")
    check("video analyzer + signals", r["ok"] and r["result"]["signals"]["monetized"] is True and abs(r["result"]["like_rate"] - 50 / 1200) < 1e-3, str(r)[:300])
    r = tool("monetization", video="vid00000001")
    check("monetization check (no ads)", r["ok"] and r["result"]["monetized"] is False and r["result"]["keywords"] == ["k1", "k2"])
    r = tool("keyword", keyword="minecraft horror")
    check("keyword analyzer", r["ok"] and len(r["result"]["results"]) == 50 and 0 <= r["result"]["difficulty"] <= 100 and r["result"]["titles_words"])
    r = tool("rank", keyword="minecraft", target="vid00000004")
    check("rank checker finds video", r["ok"] and r["result"]["rank"] == 5)
    r = tool("rank", keyword="minecraft", target="@testchannel")
    check("rank checker channel", r["ok"] and r["result"]["kind"] == "channel" and r["result"]["rank"] == 1)
    r = tool("tag-rank", video="vid00000003", max_tags=2)
    check("tag rank checker removed", not r.get("ok"))
    r = tool("sponsors", video="vid00000002")
    check("sponsor locator parses segments", r["ok"] and len(r["result"]["segments"]) == 2 and r["result"]["segments"][0]["link"].endswith("t=30s"))
    r = tool("sponsors", video="vid00000001")
    check("sponsor locator 404 -> empty", r["ok"] and r["result"]["segments"] == [])
    r = tool("comment-picker", video="vid00000001", winners=2, seed="s")
    check("comment picker unique authors", r["ok"] and r["result"]["eligible"] == 5 and len(r["result"]["winners"]) == 2)
    r2 = tool("comment-picker", video="vid00000001", winners=2, seed="s")
    check("comment picker reproducible", [w["comment_id"] for w in r["result"]["winners"]] == [w["comment_id"] for w in r2["result"]["winners"]])
    r = tool("subscribe-link", channel="@kryptic")
    check("subscribe link", r["ok"] and r["result"]["link"] == "https://www.youtube.com/@kryptic?sub_confirmation=1")
    r = tool("tags", title="I survived the moon", niche="minecraft horror")
    check("tag generator (gemini mock)", r["ok"] and r["result"]["tags"] and r["result"]["chars"] <= 500 and "," in r["result"]["paste"])
    r = tool("ad-safety", text="a calm video about crafting")
    check("ad safety (gemini mock)", r["ok"] and r["result"]["risk"] == "low")
    import base64
    import io
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (1280, 720), (200, 30, 30)).save(buf, format="JPEG")
        r = tool("thumbnail-analyzer", image_b64=base64.b64encode(buf.getvalue()).decode(), mime="image/jpeg", title="t")
        check("thumbnail analyzer metrics + ai", r["ok"] and r["result"]["metrics"]["is_16_9"] and r["result"]["ai"]["overall"] == 74)
    except ImportError:
        check("thumbnail analyzer (Pillow missing, skipped)", True)
    check("unknown tool 404", c.post("/api/tools/nope", json={}).status_code == 404)
    check("tool error is 400 with message", not tool("thumbnails", video="garbage")["ok"])

    # ------------------------------------------------------ edits + superchat
    r = c.post("/api/channels/UCtest000000000000000001/find-replace", json={"find": "Video vid0000001", "replace": "Episode 1"}).get_json()
    check("find/replace preview", r["count"] == 10 and r["quota_cost"] == 500, str(r.get("count")))
    r = c.post("/api/channels/UCtest000000000000000001/find-replace", json={"find": "Video vid00000010", "replace": "Episode 10", "apply": True}).get_json()
    check("find/replace apply hits the API", r.get("applied") == ["vid00000010"] and STATE["videos"]["vid00000010"]["snippet"]["title"] == "Episode 10", str(r))
    r = c.post("/api/channels/UCimported0000000000000001/find-replace", json={"find": "Video", "replace": "V", "apply": True}).get_json()
    check("find/replace refused without edit scope", "edit scope" in (r.get("error") or ""))
    r = c.get("/api/channels/UCtest000000000000000001/superchats").get_json()
    check("superchat summary", r["events"] == 2 and r["total"] == 25.0 and r["creator_take_home"] == 17.5)

    # -------------------------------------------------------- studio + planner
    check("thumbs index lists styles", len(c.get("/api/thumbs").get_json()["styles"]) >= 8)
    check("predict rejects empty input", c.post("/api/predict", json={}).get_json()["ok"] is False)
    _segs = _predict.parse_json3(json.dumps({"events": [{"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "hello "}, {"utf8": "there"}]},
                                                       {"tStartMs": 20000, "dDurationMs": 1000, "segs": [{"utf8": "\n"}]},
                                                       {"tStartMs": 21000, "dDurationMs": 1500, "segs": [{"utf8": "second chunk"}]}]}))
    check("json3 captions parse", _segs == [(1.0, 3.0, "hello there"), (21.0, 22.5, "second chunk")])
    _tt = _predict.parse_timedtext('<transcript><text start="4.5" dur="2">a &amp; b</text><text start="9">c</text></transcript>')
    check("timedtext captions parse", _tt == [(4.5, 6.5, "a & b"), (9.0, 9.0, "c")])
    _blk = _predict.transcript_block([(0, 2, "one"), (5, 7, "two"), (16, 18, "three"), (70, 71, "four")])
    check("transcript block groups by ~15s with m:ss stamps", _blk == "[0:00] one two\n[0:16] three\n[1:10] four")
    _long = [(i * 10.0, i * 10.0 + 5, "word " * 40) for i in range(200)]
    _lb = _predict.transcript_block(_long, cap_chars=3000)
    check("transcript block keeps the hook dense and thins the rest", len(_lb) <= 3400 and _lb.startswith("[0:00]") and "[0:40]" in _lb and "[9:00]" in _lb and _lb.count("\n") < 12)

    class _FakeStore:
        def channel(self, cid):
            return {"channel_id": cid, "title": "Fake", "scopes": "analytics", "refresh_token": "r"} if cid == "UCfake" else None
        def videos(self, cid):
            return [{"video_id": f"v{i}", "views": 1000 * (i + 1), "is_short": 0, "privacy": "public",
                     "published_at": f"2026-01-{i + 1:02d}", "duration_s": 600 + i} for i in range(12)] + \
                   [{"video_id": "s1", "views": 99, "is_short": 1, "privacy": "public", "published_at": "2026-02-01", "duration_s": 30}]
        def video(self, vid):
            return None
    _bl = _predict.channel_baseline(_FakeStore(), "UCfake", "v11", 0)
    check("channel baseline = median of the previous same-format uploads", _bl and _bl["n"] == 11 and _bl["median_views"] == 6000
          and _bl["this_views"] == 12000 and _bl["this_multiple"] == 2.0 and _bl["format"] == "long" and _bl["median_duration_s"] == 605)
    check("channel baseline needs history", _predict.channel_baseline(_FakeStore(), "UCfake", "s1", 1) is None
          and _predict.channel_baseline(_FakeStore(), "UCnope") is None)

    class _FakeAnalytics:
        def __init__(self, token):
            self.token = token
        def query(self, start, end, metrics, dimensions=None, sort=None, filters=None, max_results=None, ids=None):
            if dimensions == ["elapsedVideoTimeRatio"]:
                return {"rows": [{"elapsedVideoTimeRatio": i / 100, "audienceWatchRatio": max(0.2, 1 - i / 100)} for i in range(0, 101, 5)]}
            return {"rows": [{"video": "v11", "views": 12000, "averageViewPercentage": 41.5},
                             {"video": "v10", "views": 11000, "averageViewPercentage": 38.0},
                             {"video": "v9", "views": 10000, "averageViewPercentage": 50.0}]}
    class _FakeTokens:
        def get(self, ch):
            return "tok"
    _act = _predict.actual_retention(_FakeStore(), _FakeTokens(), "UCfake", "v11", analytics_cls=_FakeAnalytics)
    check("actual retention curve + AVD from analytics", _act and len(_act["curve"]) == 21 and _act["curve"][0] == {"pct": 0, "retention": 100.0}
          and _act["avd_pct"] == 41.5 and _act["channel_avd_pct"] == 41.5 and _act["views"] == 12000)
    check("actual retention skipped without analytics scope", _predict.actual_retention(_FakeStore(), _FakeTokens(), "UCnope", "v1", analytics_cls=_FakeAnalytics) is None)

    _fin = _predict._finalize({"curve": [{"pct": 50, "retention": 40}, {"pct": 100, "retention": 20}], "avd_pct": "38.4",
                               "packaging": {"thumbnails": [{"subject": "face", "text": "NO", "colours": "red"}]},
                               "notes": [{"t": 30, "type": "cut", "note": "x"}, {"t": 2, "type": "hook", "note": "y"}],
                               "views_multiple": [3, 0.5]}, {}, "t", 200, baseline={"median_views": 1000})["prediction"]
    check("finalize anchors the curve at 0%=100", _fin["curve"][0] == {"pct": 0, "retention": 100, "t": 0} and _fin["curve"][1]["t"] == 100)
    check("finalize flattens thumbnail concepts to strings", _fin["packaging"]["thumbnails"] == ["face — NO — red"])
    check("finalize orders notes by time", [n["ts"] for n in _fin["notes"]] == ["0:02", "0:30"])
    check("finalize expected views from the channel median", _fin["expected_views"] == {"low": 500, "high": 3000, "multiple": [0.5, 3.0]}
          and _fin["avd_pct"] == 38.4 and _fin["confidence"] == "low")
    _pr = c.post("/api/predict", json={"script": "Today we build a base. Then a creeper blows it up. The end.", "title": "Base build", "duration_s": 300}).get_json()
    check("predict from a script end-to-end (gemini mock)", _pr["ok"] and _pr["prediction"]["predicted_score"] == 71 and _pr["prediction"]["transcript_source"] == "script"
          and _pr["prediction"]["curve"][0]["retention"] == 100 and _pr["prediction"]["packaging"]["thumbnails"][0] == "face — NO — red"
          and _pr["prediction"]["expected_views"] is None and _pr["prediction"]["drop_offs"][0]["t"] == 24)
    check("predict prompt carried the script + curve rules", any("creeper blows it up" in x and "CHANNEL BASELINE" in x for x in STATE["predict_prompts"]))
    pr = c.post("/api/plan", json={"day": "2026-09-20", "title": "Plan A", "notify": True}).get_json()
    check("plan add", pr["ok"] and pr["item"]["title"] == "Plan A")
    pid = pr["item"]["id"]
    check("plan list has it", any(i["id"] == pid for i in c.get("/api/plan").get_json()["items"]))
    check("plan mark done", c.post(f"/api/plan/{pid}", json={"status": "done"}).get_json()["item"]["status"] == "done")
    check("plan delete", c.delete(f"/api/plan/{pid}").get_json()["ok"] and
          not any(i["id"] == pid for i in c.get("/api/plan").get_json()["items"]))
    c.post("/api/plan", json={"day": "2020-01-01", "title": "Overdue post", "notify": True})
    import sf.planner as _planner
    _planner.check_due(store, cfg)
    check("due plan raises a reminder alert", any(a.get("kind") == "plan_due" for a in store.alerts(include_dismissed=True)))

    # -------------------------------------------------------------- ext api
    check("ext api needs token", c.get("/api/ext/ping").status_code == 401)
    h = {"X-SF-Token": "ext-secret"}
    r = c.get("/api/ext/ping", headers=h).get_json()
    check("ext ping", r["ok"] and r["app"] == "CreatorHaven")
    check("ext preflight", c.options("/api/ext/ping").status_code == 204)
    r = c.get("/api/ext/channels?handles=@testchannel,@nobody", headers=h).get_json()
    check("ext channels resolves + caches", r["channels"]["@testchannel"]["subscribers"] == 125000 and r["channels"]["@nobody"].get("missing"))
    calls_before = len(STATE["calls"])
    c.get("/api/ext/channels?handles=@testchannel", headers=h)
    check("ext handle cache hit (no API call)", len(STATE["calls"]) == calls_before)
    r = c.post("/api/ext/realtime", headers=h, json={"channelId": "UCtest000000000000000001", "url": "/youtubei/v1/x", "samples": [{"key": "last48Hours", "value": 1234, "path": "$.a"}]}).get_json()
    check("ext realtime stored", r["ok"] and store.realtime("UCtest000000000000000001")[0]["value"] == 1234)
    det = c.get("/api/channels/UCtest000000000000000001").get_json()
    check("realtime shows in channel detail", det["realtime"][0]["key"] == "last48Hours")

    # ---- per-video report for the watch-page panel (/api/ext/video)
    r = c.post("/api/ext/video", headers=h, json={"videoId": "vid00000025"}).get_json()
    check("ext video report ok", r["ok"] and r["video"]["views"] == 90000 and r["video"]["is_short"] is False)
    check("ext video outlier high", r["outlier"] and r["outlier"] > 3)
    check("ext video ranking 1..10 (top of channel)", r["rank"] and r["rank"]["score"] == 10 and 1 <= r["rank"]["score"] <= 10)
    check("ext video overview channel", r["channel"] and r["channel"]["subs"] == 125000 and r["channel"]["videos"])
    check("ext video metrics present", r["metrics"] and r["metrics"]["median_views"])
    check("ext video revenue band ordered (longform)",
          r["revenue"]["low"] <= r["revenue"]["mid"] <= r["revenue"]["high"] and r["revenue"]["basis"] == "longform")
    check("ext video needs id", c.post("/api/ext/video", headers=h, json={}).status_code == 400)
    r2 = c.post("/api/ext/video", headers=h, json={"videoId": "vid00000007"}).get_json()
    check("ext video short basis", r2["ok"] and r2["video"]["is_short"] is True and r2["revenue"]["basis"] == "shorts")

    # ---- extension pushes a DELEGATED channel's analytics into the dashboard tables
    payload = {"channels": [{
        "channel_id": "UCdelegated0000000000001", "title": "Delegated Chan", "handle": "@deleg",
        "scopes": {"analytics": True, "monetary": False, "edit": False},
        "stats": {"subscribers": 5000, "views": 900000, "videos": 40},
        "daily": [{"day": "2026-09-07", "views": 100, "engaged_views": 60, "minutes": 300},
                  {"day": "2026-09-08", "views": 200, "engaged_views": 130, "minutes": 700}],
        "videos": [{"video_id": "delvid1", "title": "Deleg 1", "privacy": "public",
                    "published_at": "2026-09-01T00:00:00Z", "is_short": 0, "views": 1234}],
        "top": [{"video_id": "delvid1", "views": 1234, "engaged_views": 800, "minutes": 400}]}]}
    r = c.post("/api/ext/analytics", headers=h, json=payload).get_json()
    check("ext analytics ingest ok", r["ok"] and r["count"] == 1 and r["channels"] == ["UCdelegated0000000000001"])
    dch = store.channel("UCdelegated0000000000001")
    check("ext channel stored, no token, analytics scope",
          dch and not dch.get("refresh_token") and "yt-analytics.readonly" in (dch.get("scopes") or ""))
    check("ext daily rows stored", len(store.daily("UCdelegated0000000000001", "2026-09-01", "2026-09-30")) == 2)
    vs = store.video_stats("UCdelegated0000000000001", 28)
    check("ext top-video stats stored", vs and vs[0]["views"] == 1234)
    det = c.get("/api/channels/UCdelegated0000000000001?days=90").get_json()
    check("ext channel detail reports analytics scope, not revenue",
          det["scopes"]["analytics"] is True and det["scopes"]["monetary"] is False)
    r = c.post("/api/ext/probe", headers=h,
               json={"entries": [{"url": "/youtubei/v1/yta_web/join", "full": True, "sample": "{\"results\":[]}"}]}).get_json()
    check("ext probe capture ok", r["ok"] and r["stored"] == 1)
    # a partial (videos-only) push from the harvester must NOT wipe title/scopes
    c.post("/api/ext/analytics", headers=h, json={"channels": [{"channel_id": "UCdelegated0000000000001",
           "videos": [{"video_id": "delvid2", "title": "Deleg 2", "privacy": "public",
                       "published_at": "2026-09-05T00:00:00Z", "is_short": 0, "views": 55}]}]}).get_json()
    dch2 = store.channel("UCdelegated0000000000001")
    check("partial push preserves title + scope",
          dch2.get("title") == "Delegated Chan" and "yt-analytics.readonly" in (dch2.get("scopes") or ""))
    check("partial push added the video", any(v["video_id"] == "delvid2" for v in store.videos("UCdelegated0000000000001")))

    # ------------------------------------------------- strategy brain + outlier radar
    from datetime import datetime, timezone
    from sf import radar, strategist
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    qs = radar.niche_queries(store)
    check("radar auto queries from network tags", qs and all(q not in radar._GENERIC for q in qs), str(qs))
    net = radar.network_outliers(store, days=45, now=now)
    check("network outlier feed finds the 1.2M video first", net and net[0]["video_id"] == "vid00000008" and net[0]["outlier"] > 100, str(net[:1]))
    check("radar not built yet", radar.stats(store)["built"] is False and radar.brief(store)[0].startswith("(radar not built"))
    r = c.post("/api/strategy/radar/refresh", json={"wait": True, "days": 120, "queries": ["minecraft horror", "gtag"]}).get_json()
    check("radar refresh route (wait) builds both feeds", r["ok"] and r["stats"]["built"] and r["stats"]["niche"] >= 1 and r["stats"]["network"] >= 1, str(r.get("stats")))
    lat = radar.latest(store)
    check("niche feed scored against the channel's own median", any(x["video_id"] == "vid00000008" and x["median"] and x["outlier"] > 100 and x["tracked"] for x in lat["niche"]))
    check("radar units = 2 duration buckets x 100 per query + videos.list", lat["units"] == 401, str(lat["units"]))
    check("radar queries honoured", lat["queries"] == ["minecraft horror", "gtag"])
    net_txt, niche_txt, _ = radar.brief(store, seed=3)
    check("radar brief carries real ids (niche block skips tracked channels)", "id=vid00000008" in net_txt and "niche feed empty" in niche_txt)
    check("radar find by id", (radar.find(store, "vid00000008") or {}).get("outlier", 0) > 100 and radar.find(store, "nope") is None)
    r = c.get("/api/strategy/radar").get_json()
    check("radar GET", r["ok"] and r["radar"]["niche"] and r["stats"]["built"] and r["has_client"] is True)
    # settings + memory
    r = c.post("/api/strategy/settings", json={"notes": "never pitch reactions", "queries": "gorilla tag\nroblox mm2"}).get_json()
    check("strategy settings saved", r["ok"] and r["queries"] == ["gorilla tag", "roblox mm2"] and r["notes"] == "never pitch reactions")
    check("pinned queries override auto", radar.niche_queries(store) == ["gorilla tag", "roblox mm2"])
    sp = strategist.system_prompt(store, "UCtest000000000000000001", seed=1)
    check("system prompt grounds on radar + notes + playbook",
          "OUTLIER RADAR" in sp and "never pitch reactions" in sp and "id=vid00000008" in sp and "THE OUTLIER METHOD" in sp and "FOCUS CHANNEL: Test Channel" in sp)
    r = c.post("/api/strategy/chat", json={"message": "5 ideas", "channel_id": "UCtest000000000000000001"}).get_json()
    check("chat reply stripped of the memory marker", r["ok"] and "[[pitched" not in r["reply"] and "[thumb:vid00000008]" in r["reply"], str(r)[:200])
    pt = [x["title"] for x in strategist.pitched(store)]
    check("pitched titles remembered", pt == ["The 100 Night Ladder", "Second Title"], str(pt))
    h = c.get("/api/strategy/history").get_json()
    check("chat history persisted server-side", h["ok"] and len(h["history"]) == 2 and h["history"][0]["role"] == "user" and len(h["pitched"]) == 2)
    r = c.post("/api/strategy/chat", json={"message": "more", "history": []}).get_json()
    check("already-pitched block reaches the model", r["ok"] and "- The 100 Night Ladder" in STATE["strategy_prompts"][-1])
    r = c.post("/api/strategy/ideas", json={"channel_id": "UCtest000000000000000001", "n": 5, "seed_video": "vid00000008"}).get_json()
    check("ideas JSON with validated refs", r["ok"] and len(r["ideas"]) == 2 and r["ideas"][0]["ref_id"] == "vid00000008" and r["ideas"][1]["ref_id"] == "", str(r)[:200])
    check("seed video conversion instruction in prompt", "Every idea must CONVERT this specific outlier" in STATE["strategy_prompts"][-1] and "id=vid00000008" in STATE["strategy_prompts"][-1])
    check("idea titles remembered too", "I Survived 100 Nights With NO Base" in [x["title"] for x in strategist.pitched(store)])
    check("converted outlier ids remembered + hidden from the next brief",
          "vid00000008" in strategist.used_refs(store) and "id=vid00000008" not in radar.brief(store, exclude_ids=strategist.used_refs(store))[0])
    r = c.post("/api/strategy/report", json={}).get_json()
    check("outlier report keeps only real ids", r["ok"] and [p["video_id"] for p in r["picks"]] == ["vid00000008"] and r["trends"])
    r = c.post("/api/strategy/thumb", json={"idea": {"title": "T", "thumbnail_prompt": "a scene", "thumb_text": "NO BASE"}, "style": "gaming", "refs": []}).get_json()
    check("thumb route fails soft when the image model returns text", r["ok"] is False and r.get("error"))
    r = c.post("/api/strategy/reset", json={}).get_json()
    check("strategy reset clears memory", r["ok"] and strategist.pitched(store) == [] and strategist.history(store) == [])

    # ------------------------------------------------------ phone app (PWA)
    r = c.get("/m")
    check("phone app page renders", r.status_code == 200 and b'rel="manifest"' in r.data and b"/sw.js" in r.data and b"How may I help you" in r.data)
    r = c.get("/manifest.webmanifest")
    check("manifest served with the right type", r.status_code == 200 and "manifest+json" in r.headers.get("Content-Type", "") and r.get_json(force=True)["start_url"] == "/m")
    r = c.get("/sw.js")
    check("service worker at root scope", r.status_code == 200 and b"-m-v" in r.data and "javascript" in r.headers.get("Content-Type", ""))
    r = c.get("/", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"})
    check("phones are redirected to /m", r.status_code == 302 and r.headers["Location"].endswith("/m"))
    r = c.get("/?desktop=1", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"})
    check("?desktop=1 keeps a phone on the full site", r.status_code == 200)
    r = c.get("/", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128"})
    check("desktop stays on the full site", r.status_code == 200)

    # ------------------------------------------------------------- settings
    r = c.post("/api/settings", json={"sync_minutes": 15, "discord_webhook_url": "", "google": {"client_id": "newid"}}).get_json()
    check("settings save", r["ok"] and cfg["sync_minutes"] == 15 and cfg["google"]["client_id"] == "newid")
    c.delete("/api/channels/UCimported0000000000000001")
    check("channel delete", store.channel("UCimported0000000000000001") is None and not store.videos("UCimported0000000000000001"))
    check("no internet reached", all(("127.0.0.1" in x[1]) or x[1].startswith("/") for x in STATE["calls"]))

    # ------------------------------------------- hosted owner account (public site)
    import copy as _copy
    tmp_h = Path(tempfile.mkdtemp())
    cfg_h = _copy.deepcopy(cfg)
    cfg_h.update({"base_url": "https://creatorhaven.example.com", "database": str(tmp_h / "h.sqlite3"),
                  "owner_email": "Owner@Site.com", "owner_password": "", "sync_minutes": 0})
    store_h = Store(cfg_h["database"])
    ch = app_module.create_app(cfg_h, store_h, bases).test_client()
    r = ch.post("/api/auth/signup", json={"email": "owner@site.com", "password": "stranger123", "name": "x"})
    check("hosted: signing up with OWNER_EMAIL cannot claim the owner", r.status_code == 403 and store_h.owner_unclaimed())
    r = ch.post("/api/auth/signup", json={"email": "first@x.com", "password": "firstpass1", "name": "F"}).get_json()
    check("hosted: the first signup is not the owner",
          r["ok"] and r["user"]["id"] != 1 and ch.get("/api/status").get_json()["is_owner"] is False)
    cfg_h0 = dict(cfg_h, database=str(tmp_h / "h0.sqlite3"), owner_email="")
    ch0 = app_module.create_app(cfg_h0, Store(cfg_h0["database"]), bases).test_client()
    r = ch0.post("/api/auth/signup", json={"email": "anyone@x.com", "password": "anyonepass1", "name": "A"}).get_json()
    check("hosted without OWNER_EMAIL: nobody becomes the owner by signing up", r["ok"] and r["user"]["id"] != 1)
    cfg_h2 = dict(cfg_h, database=str(tmp_h / "h2.sqlite3"), owner_password="correct horse battery")
    store_h2 = Store(cfg_h2["database"])
    ch2 = app_module.create_app(cfg_h2, store_h2, bases).test_client()
    check("hosted: OWNER_PASSWORD creates the owner account at boot",
          (store_h2.get_user(1) or {}).get("email") == "owner@site.com" and not store_h2.owner_unclaimed())
    check("hosted: nobody can sign up with the owner's address",
          ch2.post("/api/auth/signup", json={"email": "OWNER@site.com", "password": "stranger123", "name": "x"}).status_code == 409)
    r = ch2.post("/api/auth/login", json={"email": "owner@site.com", "password": "correct horse battery"}).get_json()
    st = ch2.get("/api/status").get_json()
    check("hosted: the owner signs in with OWNER_PASSWORD and gets an extension key",
          r["ok"] and st["is_owner"] is True and st["user"]["id"] == 1 and bool(st.get("ext_token")))
    cfg_h3 = dict(cfg_h2, owner_password="a brand new pass")
    ch3 = app_module.create_app(cfg_h3, store_h2, bases).test_client()
    ok_new = ch3.post("/api/auth/login", json={"email": "owner@site.com", "password": "a brand new pass"}).status_code == 200
    ok_old = ch3.post("/api/auth/login", json={"email": "owner@site.com", "password": "correct horse battery"}).status_code == 200
    check("hosted: a changed OWNER_PASSWORD resets the owner's password on the next boot", ok_new and not ok_old)

    # ------------------------------------ exact windows, the daily job, YouTube's 30-day rule
    import sf.search as _sm
    _today_s = _sm._today()
    _day = lambda n: (__import__("datetime").datetime.strptime(_today_s, "%Y-%m-%d") - _td(days=n)).strftime("%Y-%m-%d")
    _linked = next((ch for ch in store.all_channels() if ch.get("refresh_token")), None)
    _linked_id = _linked["channel_id"] if _linked else "UCnone"
    for _cid in ("UCpublicold00000000000001", _linked_id):
        for _n in (40, 31, 29, 1):
            store._exec("INSERT OR REPLACE INTO channel_snapshots VALUES (?,?,?,?,?,?)", (_cid, _day(_n), 1, 1000 - _n, 1, 0))
    store._exec("INSERT OR REPLACE INTO video_snapshots VALUES ('UCpublicold00000000000001','vOld',?,5)", (_day(45),))
    _pg = _sm.purge_unauthorized_stats(store)
    _left_pub = [r["day"] for r in store._all("SELECT day FROM channel_snapshots WHERE channel_id='UCpublicold00000000000001' ORDER BY day")]
    _left_own = store._one("SELECT COUNT(*) AS n FROM channel_snapshots WHERE channel_id=? AND day IN (?,?)", (_linked_id, _day(40), _day(31)))["n"]
    check("30-day rule: other channels' counters older than 30 days are deleted, recent ones kept",
          _left_pub == [_day(29), _day(1)] and _pg["channel_snapshots"] >= 2 and _pg["video_snapshots"] >= 1)
    check("...a channel whose owner linked it keeps its history", _linked is not None and _left_own == 2)
    store.set_setting("lookup:UCstale:30", {"ts": time.time() - 40 * 86400, "data": {}})
    store.set_setting("lookup:UCfresh:30", {"ts": time.time(), "data": {}})
    check("stale cached lookups are dropped, fresh ones kept",
          _sm.purge_stale_caches(store) >= 1 and store.get_setting("lookup:UCstale:30") is None and store.get_setting("lookup:UCfresh:30") is not None)
    # a linked channel's windows come from YouTube Analytics (the numbers Studio shows)
    store._exec("DELETE FROM daily WHERE channel_id=?", (_linked_id,))
    for _n in range(1, 400):
        store._exec("INSERT OR REPLACE INTO daily(channel_id, day, views) VALUES (?,?,?)", (_linked_id, _day(_n), 100))
    _an = _sm._view_windows(_cat, {"views": 1, "videos": 1}, store, _linked_id)
    check("a linked channel's windows are YouTube Analytics' exact numbers",
          _an["28d"]["method"] == "analytics" and _an["28d"]["views"] == 2800 and _an["1yr"]["views"] == 36500 and _an["7d"]["views"] == 700)
    _pw = _sm._view_windows(_cat, {"views": 1, "videos": 1}, store, "UCpublicold00000000000001")
    check("any other channel: 30-day retention, the long windows are estimates for good",
          _pw["3mo"]["method"] == "estimate" and _pw["3mo"]["beyond_retention"] and _pw["3mo"]["retention_days"] == 30)
    # the daily job: every followed channel read once a UTC day, behind an open idempotent endpoint
    _sm.follow(store, "UC0followed0000000000001")
    store.set_setting("daily_job", {})
    anon_c = application.test_client()
    r1 = anon_c.get("/api/cron/daily")
    _deadline = time.time() + 10
    while time.time() < _deadline and (store.get_setting("daily_job") or {}).get("state") != "done":
        time.sleep(0.1)
    _dj = store.get_setting("daily_job") or {}
    r2 = anon_c.get("/api/cron/daily").get_json()
    _snap = store._one("SELECT COUNT(*) AS n FROM channel_snapshots WHERE channel_id='UC0followed0000000000001' AND day=?", (_today_s,))["n"]
    check("the daily job reads every followed channel (open endpoint, no login needed)",
          r1.status_code == 200 and r1.get_json()["ran"] and _dj.get("state") == "done" and _snap == 1
          and _dj["snapshots"]["channels"] >= 2)
    check("...and runs at most once a day", r2["ran"] is False and r2["state"] == "done")
    _js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "app.js"), encoding="utf-8").read()
    check("the Search page says what each window number is", all(x in _js for x in ("exact · YouTube Analytics", "measured · exact", "usually within", "exact in", "rough estimate", "interpolated across")))

    srv.shutdown()
    n_ok = sum(1 for _, ok in CHECKS if ok)
    print(f"\n{'ALL PASS' if n_ok == len(CHECKS) else 'FAILURES'} {n_ok}/{len(CHECKS)}")
    return 0 if n_ok == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
