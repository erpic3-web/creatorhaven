"""Hermetic selftest: a mock Google (OAuth token endpoint, Data API v3, Analytics
API v2), mock Gemini, mock SponsorBlock and mock player endpoint on loopback,
plus a network guard that fails any request to a non-loopback host.

    python test/selftest.py
"""
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

from sf import alerts, api_analytics, api_youtube, gemini, innertube, metrics, oauth, sync, tools  # noqa: E402
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

STATE = {"videos": {}, "calls": [], "analytics_403": None, "engaged_400": False, "strategy_prompts": []}


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
    cfg = {"site_password": "pw", "secret_key": "k" * 32, "port": 5999, "base_url": "http://127.0.0.1:5999",
           "database": str(tmp / "t.sqlite3"), "gemini_api_key": "gem", "youtube_api_key": "",
           "google": {"client_id": "cid", "client_secret": "csec", "client_type": "desktop"},
           "discord_webhook_url": f"{base}/hook", "scan_per_channel": 200, "analytics_days": 30, "sync_minutes": 0,
           "ext_token": "ext-secret"}
    bases = {"api_base": f"{base}/youtube/v3", "analytics_base": f"{base}/analytics/v2", "token_base": base,
             "auth_base": f"{base}/auth"}
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
    check("residual: fresh video (age<window) counts its whole total",
          metrics.video_window_views(40000, 5, 28, 8.0) == 40000)
    _arlo = metrics.video_window_views(2_909_414, 307, 28, 19.0)     # real last-28d ~17,790
    check("residual: Arlo banger last-28d ~= 17k", 14000 <= _arlo <= 22000)
    _flop = metrics.video_window_views(24_933, 115, 28, 0.35)        # real last-28d ~5,000
    check("residual: flop last-28d flatter/larger share", 2500 <= _flop <= 6500)
    check("residual: banger front-loads more than flop (less residual share)",
          _arlo / 2_909_414 < _flop / 24_933)
    # back-catalogue damping calibrated to two real channels (fast uploader keeps more back-catalogue
    # than a slow one, whose un-sampled videos are older/deader); monotonic decreasing in reach
    check("residual: back-damp calibration points (pixii ~0.85, erik ~0.31)",
          abs(metrics._back_damp(70) - 0.849) < 0.02 and abs(metrics._back_damp(312) - 0.308) < 0.02)
    check("residual: back-damp falls with sample reach, clamped",
          metrics._back_damp(0) >= metrics._back_damp(200) >= metrics._back_damp(1000) == 0.25)
    _cwv = metrics.channel_window_views(
        [{"video_id": "x", "views": 500000, "published_at": "2026-01-01T00:00:00Z", "is_short": 0, "privacy": "public"}],
        28, now=_now, outlier_by_id={"x": 25.0})
    check("channel_window_views returns parts", "views" in _cwv and _cwv["views"] > 0 and "boost" in _cwv)
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

    # -------------------------------------------------------- login + status
    r = c.get("/api/status").get_json()
    check("status public before login", r["authed"] is False and "ext_token" not in r)
    check("api gated", c.get("/api/channels").status_code == 401)
    check("wrong password rejected", c.post("/api/login", json={"password": "nope"}).status_code == 403)
    check("login ok", c.post("/api/login", json={"password": "pw"}).get_json()["ok"])
    r = c.get("/api/status").get_json()
    check("status after login", r["authed"] and r["ext_token"] == "ext-secret" and r["channels"] == 0)
    check("index served", b"CreatorHaven" in c.get("/").data)

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
    check("sync pulls analytics daily", t["daily"] == 30 and t["analytics"] == "ok", str(t["errors"]))
    check("imported channel lacks analytics scope", by["UCimported0000000000000001"]["analytics"] == "needs_scope" and by["UCimported0000000000000001"]["videos"] == 49)
    d = store.daily("UCtest000000000000000001", "2000-01-01", "2999-01-01")
    check("daily rows carry engaged views", len(d) == 30 and d[0]["engaged_views"] == 800 and d[0]["revenue"] == 12.5)
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
    check("engagedViews 400 falls back to views", r["analytics"] == "ok" and r["daily"] == 30, str(r["errors"]))
    STATE["engaged_400"] = False
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
    check("tag rank checker", r["ok"] and r["result"]["tags_checked"] == 2 and r["result"]["ranks"][0]["rank"] == 4)
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

    srv.shutdown()
    n_ok = sum(1 for _, ok in CHECKS if ok)
    print(f"\n{'ALL PASS' if n_ok == len(CHECKS) else 'FAILURES'} {n_ok}/{len(CHECKS)}")
    return 0 if n_ok == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
