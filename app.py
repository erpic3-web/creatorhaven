"""CreatorHaven web app (Flask). Run with run.bat or `python app.py`.

create_app() is a factory so the selftest can boot it against a temp database
and mock Google endpoints.
"""
import base64
import json
import os
import re as _re
import secrets
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sf import alerts as alerts_mod          # noqa: E402
from sf import (api_analytics, api_youtube, config, gemini, http, imagegen, innertube, metrics, persist, radar,  # noqa: E402
                oauth, planner, predict, strategist, sync, thumbs, tools)
from sf.store import Store                    # noqa: E402

OPEN_PATHS = ("/api/login", "/api/status", "/static/", "/api/ext/", "/oauth/cb", "/favicon.ico", "/thumbs/")


def create_app(cfg=None, store=None, bases=None, start_background=False):
    cfg = cfg or config.load()
    if store is None:
        # hosted (Render): the SQLite file is restored from Postgres before it is opened
        if persist.configure(cfg.get("database_url")):
            persist.restore(cfg["database"])
        store = Store(cfg["database"])
    bases = bases or {}
    app = Flask(__name__, static_folder=str(ROOT / "static"), template_folder=str(ROOT / "templates"))
    app.secret_key = cfg["secret_key"]
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024   # allow MP4 uploads for the video predictor
    tokens = sync.TokenCache(cfg, token_base=bases.get("token_base", oauth.TOKEN_BASE))
    state = {"sync_running": False, "sync_started": None, "log": []}
    lock = threading.Lock()
    app.sf = {"cfg": cfg, "store": store, "tokens": tokens, "bases": bases, "state": state}

    def log(msg):
        state["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
        del state["log"][:-200]

    def api_base():
        return bases.get("api_base", api_youtube.API_BASE)

    def public_client():
        if cfg.get("youtube_api_key"):
            return api_youtube.YouTube(api_key=cfg["youtube_api_key"], quota_cb=sync.quota_cb(store), base=api_base())
        tok = sync.any_access_token(store, cfg, tokens)
        if tok:
            return api_youtube.YouTube(access_token=tok, quota_cb=sync.quota_cb(store), base=api_base())
        return None

    def channel_client(channel_id):
        ch = store.channel(channel_id)
        if not ch or not ch.get("refresh_token"):
            raise tools.ToolError("Channel is not linked")
        return ch, api_youtube.YouTube(access_token=tokens.get(ch), quota_cb=sync.quota_cb(store), base=api_base())

    def run_sync(only=None):
        with lock:
            if state["sync_running"]:
                return False
            state["sync_running"] = True
            state["sync_started"] = time.time()
        try:
            log("sync started")
            try:
                planner.check_due(store, cfg)   # raise reminders for due content-plan items
            except Exception as e:
                log(f"planner check failed: {e}")
            if only:
                ch = store.channel(only)
                res = [sync.sync_channel(store, cfg, ch, tokens, api_base=api_base(),
                                         analytics_base=bases.get("analytics_base", api_analytics.ANALYTICS_BASE), log=log)]
                store.set_setting("last_sync", {"at": time.time(), "summary": res})
            else:
                res = sync.sync_all(store, cfg, tokens, log=log, api_base=api_base(),
                                    analytics_base=bases.get("analytics_base", api_analytics.ANALYTICS_BASE))
            log(f"sync done: {sum(r['videos'] for r in res)} videos, {sum(r['alerts'] for r in res)} new alerts")
            return res
        finally:
            state["sync_running"] = False

    def background_loop():
        while True:
            mins = int(cfg.get("sync_minutes") or 0)
            if mins <= 0:
                time.sleep(60)
                continue
            last = (store.get_setting("last_sync") or {}).get("at", 0)
            if time.time() - last >= mins * 60:
                try:
                    run_sync()
                except Exception as e:  # never let the loop die
                    log(f"background sync error: {e}")
            time.sleep(30)

    if start_background:
        threading.Thread(target=background_loop, daemon=True, name="sf-sync").start()
        persist.start_timer(store, cfg.get("persist_minutes") or 10)

    # every write-shaped API call schedules a durable push (debounced, off the request thread)
    _push_at = {"t": 0.0, "armed": False}

    def schedule_push(delay=8):
        if not persist.enabled() or _push_at["armed"]:
            return
        _push_at["armed"] = True

        def later():
            time.sleep(delay)
            _push_at["armed"] = False
            persist.push(store)
        threading.Thread(target=later, daemon=True, name="sf-persist-push").start()

    @app.after_request
    def _durable(resp):
        if request.method in ("POST", "PUT", "DELETE") and request.path.startswith("/api/") and resp.status_code < 400:
            schedule_push()
        return resp
    app.sf["schedule_push"] = schedule_push

    # ---------------------------------------------------------------- auth
    @app.before_request
    def gate():
        if request.method == "OPTIONS":
            return _cors(("", 204))
        p = request.path
        if p.startswith("/api/ext/"):
            if request.headers.get("X-SF-Token") != cfg["ext_token"]:
                return _cors((jsonify({"ok": False, "error": "bad token"}), 401))
            return None
        if not cfg.get("site_password"):
            return None
        if any(p.startswith(o) for o in OPEN_PATHS) or session.get("auth"):
            return None
        if p.startswith("/api/"):
            return jsonify({"error": "login required"}), 401
        return None  # the SPA shows the login card itself

    def _cors(resp):
        r = app.make_response(resp)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type, X-SF-Token"
        r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return r

    @app.after_request
    def cors(resp):
        if request.path.startswith("/api/ext/"):
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-SF-Token"
        return resp

    @app.post("/api/login")
    def login():
        pw = (request.get_json(silent=True) or {}).get("password", "")
        if cfg.get("site_password") and not secrets.compare_digest(pw, cfg["site_password"]):
            return jsonify({"ok": False, "error": "wrong password"}), 403
        session["auth"] = True
        return jsonify({"ok": True})

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    # -------------------------------------------------------------- pages
    @app.get("/")
    def index():
        # phones land on the app-shaped interface; ?desktop=1 forces the full site
        ua = request.headers.get("User-Agent", "")
        if request.args.get("desktop") != "1" and _re.search(r"Android|iPhone|iPod|Mobile Safari|Windows Phone", ua) \
                and "iPad" not in ua:
            return redirect("/m")
        return render_template("index.html", app_name=config.APP_NAME, version=config.APP_VERSION)

    # ------------------------------------------------------ the phone app (PWA)
    @app.get("/m")
    def mobile():
        return render_template("mobile.html", app_name=config.APP_NAME, version=config.APP_VERSION)

    @app.get("/manifest.webmanifest")
    def manifest():
        r = send_from_directory(str(ROOT / "static"), "manifest.webmanifest")
        r.headers["Content-Type"] = "application/manifest+json"
        return r

    @app.get("/sw.js")
    def service_worker():
        r = send_from_directory(str(ROOT / "static"), "sw.js")   # root path = root scope
        r.headers["Content-Type"] = "application/javascript"
        r.headers["Cache-Control"] = "no-cache"
        return r

    @app.get("/api/status")
    def status():
        pub = config.public_view(cfg)
        pub["authed"] = bool(session.get("auth")) or not cfg.get("site_password")
        pub["hosted"] = hosted()
        pub["persistence"] = persist.status()
        if pub["authed"]:
            pub["channels"] = len(store.channels())
            pub["quota_today"] = store.quota(sync.today())
            pub["last_sync"] = store.get_setting("last_sync")
            pub["sync_running"] = state["sync_running"]
            pub["alerts_open"] = len(store.alerts())
        else:
            pub.pop("ext_token", None)
        return jsonify(pub)

    @app.get("/api/log")
    def get_log():
        return jsonify({"lines": state["log"][-100:]})

    # ------------------------------------------------------------ channels
    @app.get("/api/channels")
    def channels():
        return jsonify({"channels": store.channels(), "combined": metrics.combined_channel_stats(store.channels())})

    @app.post("/api/channels/import")
    def channels_import():
        imported = sync.import_schedule_bot(store, cfg)
        return jsonify({"imported": imported})

    @app.delete("/api/channels/<cid>")
    def channel_delete(cid):
        store.delete_channel(cid)     # local only: the token stays valid for the schedule bot
        tokens.invalidate(cid)
        return jsonify({"ok": True})

    @app.post("/api/sync")
    def api_sync():
        only = (request.get_json(silent=True) or {}).get("channel_id")
        if state["sync_running"]:
            return jsonify({"started": False, "running": True})
        threading.Thread(target=run_sync, args=(only,), daemon=True).start()
        return jsonify({"started": True})

    @app.post("/api/sync/now")
    def api_sync_now():
        """Synchronous sync (used by the selftest and the CLI)."""
        res = run_sync((request.get_json(silent=True) or {}).get("channel_id"))
        return jsonify({"result": res})

    @app.get("/api/sync/status")
    def sync_status():
        return jsonify({"running": state["sync_running"], "started": state["sync_started"],
                        "last": store.get_setting("last_sync"), "log": state["log"][-30:]})

    @app.get("/api/overview")
    def api_overview():
        days = int(request.args.get("days", 28))
        return jsonify(sync.overview(store, cfg, days))

    @app.get("/api/channels/<cid>")
    def channel_detail(cid):
        import sf.search as search_mod
        ch = store.channel(cid)
        if not ch:
            return jsonify({"error": "unknown channel"}), 404
        days = int(request.args.get("days", 28))   # 0 = lifetime
        ch["stats"] = json.loads(ch.pop("stats_json") or "{}")
        ch["has_token"] = bool(ch.pop("refresh_token", None))
        vids = store.videos(cid)
        series = sync.channel_window(store, cid, days) if days else []
        top_stats = {r["video_id"]: r for r in store.video_stats(cid, 28)}
        by_id = {v["video_id"]: v for v in vids}
        top = []
        for vid, r in top_stats.items():
            v = by_id.get(vid, {})
            top.append({"video_id": vid, "title": v.get("title") or vid, "thumb": v.get("thumb"),
                        "views_28d": r["views"], "engaged_28d": r["engaged_views"], "minutes_28d": r["minutes"],
                        "published_at": v.get("published_at")})
        top.sort(key=lambda r: -(r["views_28d"] or 0))
        outliers = metrics.outlier_scores(vids)
        for v in vids:
            v["outlier"] = outliers.get(v["video_id"])
            v.pop("description", None)
        try:
            growth = search_mod.growth(store, cid)
            growth["window"] = search_mod.window_growth(store, cid, days or None)
        except Exception:
            growth = {"days": 0, "rows": [], "window": None}
        # channel-wide view delta for the window = evergreen/residual views included
        win_views = growth["window"].get("views") if (days and isinstance(growth.get("window"), dict)) else None
        public = metrics.public_window_stats(vids, days or None, channel_stats=ch["stats"], window_views=win_views)
        return jsonify({"channel": ch, "days": days, "series": series, "totals": metrics.sum_totals(series),
                        "est_revenue": metrics.estimated_revenue(vids), "public": public, "growth": growth,
                        "top": top[:50], "videos": vids, "latest": metrics.latest_ranking(vids),
                        "scheduled": [dict(v, publish_dt=None) for v in metrics.scheduled(vids)],
                        "realtime": store.realtime(cid),
                        "scopes": {"analytics": oauth.has_scope(ch.get("scopes"), oauth.SCOPE_ANALYTICS),
                                   "monetary": oauth.has_scope(ch.get("scopes"), oauth.SCOPE_MONETARY),
                                   "edit": oauth.has_scope(ch.get("scopes"), oauth.SCOPE_FORCE_SSL)}})

    @app.get("/api/calendar")
    def api_calendar():
        by = {c["channel_id"]: (c["title"], store.videos(c["channel_id"])) for c in store.channels()}
        return jsonify(metrics.calendar(by, days=int(request.args.get("days", 60))))

    # -------------------------------------------------------------- alerts
    @app.get("/api/alerts")
    def api_alerts():
        try:
            planner.check_due(store, cfg)   # surface any due content-plan reminders
        except Exception:
            pass
        return jsonify({"alerts": store.alerts(include_dismissed=request.args.get("all") == "1"), "kinds": alerts_mod.KINDS})

    @app.post("/api/alerts/<int:aid>/dismiss")
    def alert_dismiss(aid):
        store.dismiss_alert(aid)
        return jsonify({"ok": True})

    @app.post("/api/alerts/deliver")
    def alerts_deliver():
        n = alerts_mod.deliver_discord(store, cfg.get("discord_webhook_url"))
        return jsonify({"delivered": n})

    # --------------------------------------------------------------- oauth
    def hosted():
        return not cfg["base_url"].startswith(("http://127.0.0.1", "http://localhost"))

    def redirect_uri():
        if cfg["google"].get("client_type", "desktop") == "desktop" and hosted():
            # a Desktop OAuth client only accepts loopback redirects: Google will land the user's
            # browser on 127.0.0.1 (dead, or the local site), and they paste the code from the URL
            return f"http://127.0.0.1:{cfg['port']}/oauth/cb"
        return f"{cfg['base_url'].rstrip('/')}/oauth/cb"

    @app.get("/api/oauth/start")
    def oauth_start():
        if not (cfg["google"]["client_id"] and cfg["google"]["client_secret"]):
            return jsonify({"error": "Google OAuth client is not configured (data/config.json → google)"}), 400
        sets = [s for s in (request.args.get("sets") or "").split(",") if s] or oauth.DEFAULT_SETS
        url, st = oauth.auth_url(cfg, redirect_uri(), sets, auth_base=bases.get("auth_base", oauth.AUTH_BASE))
        session["oauth_state"] = st
        return jsonify({"url": url, "redirect_uri": redirect_uri(), "paste_code": redirect_uri().startswith("http://127.0.0.1") and hosted()})

    def finish_link(code):
        tok = oauth.exchange_code(cfg, code, redirect_uri(), token_base=bases.get("token_base", oauth.TOKEN_BASE))
        return sync.link_channel(store, cfg, tok, api_base=api_base())

    @app.get("/oauth/cb")
    def oauth_cb():
        err = request.args.get("error")
        if err:
            return f"<h2>Google said: {err}</h2><p>Close this tab and try again.</p>", 400
        if request.args.get("state") != session.get("oauth_state"):
            return "<h2>State mismatch</h2><p>Start the link again from the app.</p>", 400
        try:
            ch = finish_link(request.args.get("code", ""))
        except Exception as e:
            return f"<h2>Link failed</h2><pre>{e}</pre>", 500
        return redirect(f"/?linked={ch['title']}")

    @app.post("/api/oauth/redeem")
    def oauth_redeem():
        code = (request.get_json(silent=True) or {}).get("code", "").strip()
        if not code:
            return jsonify({"error": "paste the code from the address bar"}), 400
        try:
            ch = finish_link(code)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"linked": ch["title"], "channel_id": ch["channel_id"]})

    # ------------------------------------------------------------ settings
    EDITABLE = ("discord_webhook_url", "sync_minutes", "scan_per_channel", "analytics_days", "site_password",
                "youtube_api_key", "base_url")

    @app.post("/api/settings")
    def settings_save():
        body = request.get_json(silent=True) or {}
        file_cfg = {}
        if config.CONFIG_PATH.exists():
            try:
                file_cfg = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8-sig"))
            except Exception:
                file_cfg = {}
        for k in EDITABLE:
            if k in body:
                v = body[k]
                if k in ("sync_minutes", "scan_per_channel", "analytics_days"):
                    v = int(v or 0)
                cfg[k] = v
                file_cfg[k] = v
        if "google" in body and isinstance(body["google"], dict):
            g = body["google"]
            for k in ("client_id", "client_secret", "client_type"):
                if g.get(k):
                    cfg["google"][k] = g[k]
                    file_cfg.setdefault("google", {})[k] = g[k]
        if body.get("gemini_api_key"):
            cfg["gemini_api_key"] = body["gemini_api_key"]
            file_cfg["gemini_api_key"] = body["gemini_api_key"]
        if not os.environ.get("SF_NO_PERSIST"):
            config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            config.CONFIG_PATH.write_text(json.dumps(file_cfg, indent=2), encoding="utf-8")
        return jsonify({"ok": True, "status": config.public_view(cfg)})

    # ---------------------------------------------------------- superchats
    @app.get("/api/channels/<cid>/superchats")
    def superchats(cid):
        try:
            ch, yt = channel_client(cid)
            events = yt.super_chat_events()
        except tools.ToolError as e:
            return jsonify({"error": str(e)}), 400
        except http.HttpError as e:
            return jsonify({"error": e.message or str(e)}), 400
        return jsonify(tools.superchat_summary(events))

    # ------------------------------------------------------- find/replace
    @app.post("/api/channels/<cid>/find-replace")
    def find_replace(cid):
        body = request.get_json(silent=True) or {}
        find, repl = body.get("find", ""), body.get("replace", "")
        if not find:
            return jsonify({"error": "find text is empty"}), 400
        try:
            ch, yt = channel_client(cid)
        except tools.ToolError as e:
            return jsonify({"error": str(e)}), 400
        ids = [v["video_id"] for v in store.videos(cid)]
        full = yt.videos(ids[: int(body.get("limit", 200))])
        preview = tools.find_replace_preview(full, find, repl, body.get("in_title", True),
                                             body.get("in_description", True), body.get("case_sensitive", False))
        if not body.get("apply"):
            return jsonify({"preview": preview, "count": len(preview), "quota_cost": 50 * len(preview)})
        if not oauth.has_scope(ch.get("scopes"), oauth.SCOPE_FORCE_SSL):
            return jsonify({"error": "This channel is linked without the edit scope; re-link with 'edit' checked."}), 400
        by_id = {v["video_id"]: v for v in full}
        done, errors = [], []
        for p in preview:
            v = by_id[p["video_id"]]
            try:
                yt.update_video_snippet(v["video_id"], p["changes"].get("title", v["title"]),
                                        p["changes"].get("description", v.get("description") or ""),
                                        v.get("tags"), v.get("category_id") or "22")
                done.append(p["video_id"])
            except http.HttpError as e:
                errors.append({"video_id": p["video_id"], "error": e.message or str(e)})
        return jsonify({"applied": done, "errors": errors})

    # --------------------------------------------------------------- tools
    def _yt_or_400():
        yt = public_client()
        if not yt:
            raise tools.ToolError("Link at least one channel (or add a YouTube API key) so the tools can read public data")
        return yt

    TOOL_FUNCS = {
        "thumbnails": lambda b: tools.check_thumb_sizes(tools.thumbnails(b.get("video"))),
        "channel-images": lambda b: tools.channel_images(_yt_or_400(), b.get("channel")),
        "channel-id": lambda b: tools.channel_id_finder(_yt_or_400(), b.get("channel")),
        "comment-export": lambda b: tools.comment_export(_yt_or_400(), b.get("video"), int(b.get("limit", 1000)), b.get("order", "relevance")),
        "playlist-export": lambda b: tools.playlist_export(_yt_or_400(), b.get("playlist"), int(b.get("limit", 1000))),
        "channel-backup": lambda b: tools.channel_backup(_yt_or_400(), b.get("channel"), int(b.get("limit", 2000))),
        "video-analyzer": lambda b: tools.video_analyzer(_yt_or_400(), b.get("video")),
        "playlist-analyzer": lambda b: tools.playlist_analyzer(_yt_or_400(), b.get("playlist")),
        "monetization": lambda b: tools.monetization_check(b.get("video")),
        "keyword": lambda b: tools.keyword_analyzer(_yt_or_400(), b.get("keyword"), b.get("region") or None),
        "rank": lambda b: tools.rank_checker(_yt_or_400(), b.get("keyword"), b.get("target"), b.get("region") or None),
        "tag-rank": lambda b: tools.tag_rank_checker(_yt_or_400(), b.get("video"), b.get("region") or None, int(b.get("max_tags", 15))),
        "sponsors": lambda b: tools.sponsor_locator(b.get("video")),
        "comment-picker": lambda b: tools.comment_picker(_yt_or_400(), b.get("video"), int(b.get("winners", 1)), int(b.get("min_likes", 0)),
                                                         bool(b.get("unique", True)), b.get("keyword") or None, b.get("seed") or None),
        "subscribe-link": lambda b: tools.subscribe_link(b.get("channel")),
        "tags": lambda b: tools.tag_generator(cfg, b.get("title", ""), b.get("description", ""), b.get("niche", ""), int(b.get("count", 25))),
        "ad-safety": lambda b: tools.ad_safety(cfg, b.get("text", "")),
        "thumbnail-analyzer": lambda b: tools.thumbnail_analyzer(cfg, base64.b64decode(b.get("image_b64", "")), b.get("mime", "image/jpeg"), b.get("title", "")),
    }

    # ------------------------------------------------- channel search (ViewStats-style)
    @app.get("/api/search/channels")
    def search_channels():
        import sf.search as search_mod
        q = (request.args.get("q") or "").strip()
        yt = public_client()
        if not yt:
            return jsonify({"ok": False, "error": "No YouTube client: link a channel or set a Data API key in Settings."}), 400
        try:
            res = search_mod.search_channels(store, yt, q, limit=int(request.args.get("limit", 10)))
            return jsonify({"ok": True, **res})
        except http.HttpError as e:
            return jsonify({"ok": False, "error": e.message or str(e), "status": e.status}), 502
        except Exception as e:
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

    @app.get("/api/lookup/channel/<cid>")
    def lookup_channel(cid):
        import sf.search as search_mod
        yt = public_client()
        if not yt:
            return jsonify({"ok": False, "error": "No YouTube client: link a channel or set a Data API key in Settings."}), 400
        try:
            res = search_mod.lookup_channel(store, yt, cid, n_videos=int(request.args.get("videos", 30)),
                                            force=request.args.get("refresh") == "1")
            return jsonify({"ok": True, **res})
        except http.HttpError as e:
            return jsonify({"ok": False, "error": e.message or str(e), "status": e.status}), 502
        except Exception as e:
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

    @app.post("/api/tools/<name>")
    def run_tool(name):
        fn = TOOL_FUNCS.get(name)
        if not fn:
            return jsonify({"error": "unknown tool"}), 404
        body = request.get_json(silent=True) or {}
        try:
            return jsonify({"ok": True, "result": fn(body)})
        except tools.ToolError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except gemini.GeminiError as e:
            return jsonify({"ok": False, "error": f"Gemini: {e}"}), 502
        except http.HttpError as e:
            return jsonify({"ok": False, "error": e.message or str(e), "status": e.status}), 502
        except Exception as e:
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

    @app.get("/api/tools")
    def list_tools():
        return jsonify({"tools": sorted(TOOL_FUNCS)})

    @app.post("/api/channels/add-public")
    def add_public():
        import re as _re
        body = request.get_json(silent=True) or {}
        refs = body.get("refs") or []
        if isinstance(refs, str):
            refs = _re.split(r"[\n,]+", refs)
        refs = [r.strip() for r in refs if r and str(r).strip()][:100]
        if not refs:
            return jsonify({"ok": False, "error": "no channel refs"}), 400
        res = sync.add_public_channels(store, cfg, refs, log=log)
        ok = "error" not in res
        n = len(res.get("added", [])) if ok else 0
        log(f"[public] add {n} channel(s); {len(res.get('failed', []))} failed")
        return jsonify({"ok": ok, **res})

    # ------------------------------------------------------- STUDIO: strategy brain
    def _gemini_guard(fn):
        if not cfg.get("gemini_api_key"):
            return jsonify({"ok": False, "error": "Add a Gemini API key in Settings to use this."}), 400
        try:
            return jsonify({"ok": True, **fn()})
        except tools.ToolError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except gemini.GeminiError as e:
            return jsonify({"ok": False, "error": f"Gemini: {e}"}), 502
        except imagegen.ImageError as e:
            return jsonify({"ok": False, "error": str(e)}), 502
        except http.HttpError as e:
            return jsonify({"ok": False, "error": e.message or str(e)}), 502
        except Exception as e:
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

    @app.post("/api/strategy/chat")
    def strategy_chat():
        b = request.get_json(silent=True) or {}
        images = []
        for im in (b.get("images") or [])[:4]:
            try:
                data = base64.b64decode(str(im.get("b64") or "").split(",")[-1])
            except Exception:
                continue
            if data:
                images.append((data, im.get("mime") or "image/jpeg"))
        return _gemini_guard(lambda: {"reply": strategist.chat(cfg, store, b.get("history") or [],
                                       b.get("message", ""), images=images or None,
                                       focus_id=b.get("channel_id") or None)})

    @app.post("/api/strategy/ideas")
    def strategy_ideas():
        b = request.get_json(silent=True) or {}
        return _gemini_guard(lambda: strategist.ideas(cfg, store, b.get("channel_id") or None,
                                                       b.get("topic", ""), int(b.get("n", 5)),
                                                       seed_video=b.get("seed_video") or None))

    @app.post("/api/strategy/report")
    def strategy_report():
        b = request.get_json(silent=True) or {}
        return _gemini_guard(lambda: strategist.outlier_report(cfg, store, b.get("channel_id") or None,
                                                                int(b.get("n", 8))))

    @app.get("/api/strategy/history")
    def strategy_history():
        return jsonify({"ok": True, "history": strategist.history(store), "pitched": strategist.pitched(store)[-60:],
                        "notes": strategist.notes(store), "queries": store.get_setting(radar.QUERIES_KEY) or [],
                        "auto_queries": radar.niche_queries(store), "radar": radar.stats(store)})

    @app.post("/api/strategy/reset")
    def strategy_reset():
        strategist.forget(store)
        return jsonify({"ok": True})

    @app.post("/api/strategy/settings")
    def strategy_settings():
        b = request.get_json(silent=True) or {}
        if "notes" in b:
            strategist.set_notes(store, str(b.get("notes") or ""))
        if "queries" in b:
            q = b.get("queries") or []
            if isinstance(q, str):
                q = _re.split(r"[\n,]+", q)
            store.set_setting(radar.QUERIES_KEY, [str(x).strip() for x in q if str(x).strip()][:12])
        return jsonify({"ok": True, "notes": strategist.notes(store),
                        "queries": store.get_setting(radar.QUERIES_KEY) or []})

    # ---- the outlier radar (network feed = store, niche feed = YouTube Data API)
    state.setdefault("radar_running", False)
    state.setdefault("radar_error", None)

    def _radar_slim(d, n=60):
        if not d:
            return None
        return {k: (d.get(k)[:n] if k in ("network", "niche") else d.get(k)) for k in
                ("ts", "days", "queries", "units", "network", "niche", "error", "took_s", "formats")}

    @app.get("/api/strategy/radar")
    def strategy_radar():
        return jsonify({"ok": True, "radar": _radar_slim(radar.latest(store)), "running": state["radar_running"],
                        "stats": radar.stats(store), "auto_queries": radar.niche_queries(store),
                        "queries": store.get_setting(radar.QUERIES_KEY) or [],
                        "has_client": public_client() is not None})

    def run_radar(queries, days, niche, formats=("long",)):
        with lock:
            if state["radar_running"]:
                return False
            state["radar_running"] = True
            state["radar_error"] = None
        try:
            radar.refresh(store, public_client() if niche else None, days=days, queries=queries, log=log, niche=niche,
                          formats=formats)
        except Exception as e:
            state["radar_error"] = f"{type(e).__name__}: {e}"
            log(f"[radar] failed: {e}")
        finally:
            state["radar_running"] = False
        return True

    @app.post("/api/strategy/radar/refresh")
    def strategy_radar_refresh():
        b = request.get_json(silent=True) or {}
        q = b.get("queries") or []
        if isinstance(q, str):
            q = _re.split(r"[\n,]+", q)
        q = [str(x).strip() for x in q if str(x).strip()][:12]
        days = max(7, min(120, int(b.get("days") or radar.DEFAULT_DAYS)))
        niche = bool(b.get("niche", True))
        formats = tuple(f for f in (b.get("formats") or ["long"]) if f in radar.FORMATS) or ("long",)
        if state["radar_running"]:
            return jsonify({"ok": False, "error": "radar refresh already running"}), 409
        if b.get("wait"):
            run_radar(q, days, niche, formats)
            return jsonify({"ok": not state["radar_error"], "error": state["radar_error"],
                            "radar": _radar_slim(radar.latest(store)), "stats": radar.stats(store)})
        threading.Thread(target=run_radar, args=(q, days, niche, formats), daemon=True, name="sf-radar").start()
        return jsonify({"ok": True, "started": True})

    @app.get("/api/strategy/radar/status")
    def strategy_radar_status():
        return jsonify({"ok": True, "running": state["radar_running"], "error": state["radar_error"],
                        "stats": radar.stats(store), "log": [l for l in state["log"][-40:] if "[radar]" in l]})

    @app.post("/api/strategy/thumb")
    def strategy_thumb():
        """Paint the thumbnail for one pitched idea with the generator, using the operator's saved
        face / character / style references so it looks like THEIR channel."""
        b = request.get_json(silent=True) or {}
        idea = b.get("idea") or {}
        style = b.get("style") or "gaming"
        subject = (idea.get("thumbnail_prompt") or idea.get("thumbnail") or idea.get("title") or "").strip()
        text = (b.get("text") if b.get("text") is not None else idea.get("thumb_text")) or ""
        refs = b.get("refs")
        if refs is None:   # default: newest face + character + two style refs
            allr = thumbs.list_refs()
            refs = ([{"kind": "face", "id": r["id"]} for r in allr.get("face", [])[:1]]
                    + [{"kind": "character", "id": r["id"]} for r in allr.get("character", [])[:1]]
                    + [{"kind": "style", "id": r["id"]} for r in allr.get("style", [])[:2]])
        extra = f'The video is titled "{idea.get("title")}".' if idea.get("title") else ""
        if not subject:
            return jsonify({"ok": False, "error": "no idea to paint"}), 400
        return _gemini_guard(lambda: thumbs.generate(cfg, style, subject, text, extra, refs))

    # ------------------------------------------------------- STUDIO: video prediction
    @app.post("/api/predict")
    def api_predict():
        b = request.get_json(silent=True) or {}
        path = (b.get("path") or "").strip()
        if path:   # a local video file on this machine (best for multi-GB recordings)
            return _gemini_guard(lambda: predict.predict_video(cfg, path, b.get("title", "")))
        return _gemini_guard(lambda: predict.predict(cfg, public_client(), b.get("source", ""),
                                     b.get("script", ""), b.get("title", ""), int(b.get("duration_s") or 0)))

    @app.post("/api/predict/upload")
    def api_predict_upload():
        if not cfg.get("gemini_api_key"):
            return jsonify({"ok": False, "error": "Add a Gemini API key in Settings to analyze video."}), 400
        f = request.files.get("video")
        if not f:
            return jsonify({"ok": False, "error": "no video file"}), 400
        import tempfile as _tf
        suffix = os.path.splitext(f.filename or "")[1] or ".mp4"
        tmp = _tf.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            f.save(tmp.name); tmp.close()
            return _gemini_guard(lambda: predict.predict_video(cfg, tmp.name, request.form.get("title", "") or (f.filename or "")))
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    # ------------------------------------------------------- STUDIO: thumbnail generator
    @app.get("/api/thumbs")
    def thumbs_index():
        return jsonify({"ok": True, "gemini": bool(cfg.get("gemini_api_key")),
                        "styles": [{"id": k, "name": v["name"], "desc": v["desc"]} for k, v in thumbs.STYLES.items()],
                        "refs": thumbs.list_refs(), "outputs": thumbs.list_outputs()})

    @app.post("/api/thumbs/ref")
    def thumbs_add_ref():
        b = request.get_json(silent=True) or {}
        if b.get("kind") not in thumbs.REF_KINDS:
            return jsonify({"ok": False, "error": "bad reference kind"}), 400
        try:
            data = base64.b64decode((b.get("image_b64") or "").split(",")[-1])
        except Exception:
            data = b""
        if not data:
            return jsonify({"ok": False, "error": "no image"}), 400
        return jsonify({"ok": True, "ref": thumbs.save_ref(b["kind"], data, b.get("mime", "image/png"))})

    @app.delete("/api/thumbs/ref/<kind>/<rid>")
    def thumbs_del_ref(kind, rid):
        return jsonify({"ok": thumbs.delete_ref(kind, rid)})

    @app.post("/api/thumbs/generate")
    def thumbs_generate():
        b = request.get_json(silent=True) or {}
        return _gemini_guard(lambda: thumbs.generate(cfg, b.get("style", "mrbeast"), b.get("subject", ""),
                                     b.get("text", ""), b.get("extra", ""), b.get("refs") or []))

    @app.post("/api/thumbs/import")
    def thumbs_import():
        b = request.get_json(silent=True) or {}
        cid = b.get("channel_id")
        if not cid:
            return jsonify({"ok": False, "error": "pick a channel"}), 400
        try:
            return jsonify({"ok": True, **thumbs.import_from_channel(store, cid)})
        except Exception as e:
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

    @app.get("/thumbs/ref/<kind>/<name>")
    def thumbs_ref_file(kind, name):
        if kind not in thumbs.REF_KINDS:
            return "", 404
        return send_from_directory(str(thumbs.REFS_DIR / kind), name)

    @app.get("/thumbs/out/<name>")
    def thumbs_out_file(name):
        return send_from_directory(str(thumbs.OUT_DIR), name)

    # ------------------------------------------------------- content planner (personal)
    @app.get("/api/plan")
    def plan_list():
        return jsonify({"ok": True, "items": planner.items(store, request.args.get("start"), request.args.get("end"))})

    @app.post("/api/plan")
    def plan_add():
        b = request.get_json(silent=True) or {}
        if not b.get("day") or not (b.get("title") or "").strip():
            return jsonify({"ok": False, "error": "day and title are required"}), 400
        return jsonify({"ok": True, "item": planner.add(store, b["day"], b["title"].strip(),
                        b.get("channel_id") or None, b.get("note", ""), b.get("notify", True))})

    @app.post("/api/plan/<int:pid>")
    def plan_update(pid):
        row = planner.update(store, pid, request.get_json(silent=True) or {})
        return jsonify({"ok": bool(row), "item": row})

    @app.delete("/api/plan/<int:pid>")
    def plan_delete(pid):
        planner.delete(store, pid)
        return jsonify({"ok": True})

    # ------------------------------------------ production ledger (Monday-style pipeline)
    def _ledger_seed():
        return {
            "title": "YouTube Master Pipeline",
            "groups": [
                {"id": "concept", "name": "Ideas", "rows": [
                    {"id": "c1", "asset": "YT-041", "title": "I Survived 100 Hours in the Backrooms", "stage": "STAGE.01",
                     "status": "idea", "priority": "high", "owner": "E.M", "due": "2026-09-18",
                     "sub": ["Ideation locked", "Outline drafted", "5 hook variants written"]},
                    {"id": "c2", "asset": "YT-042", "title": "Ranking Every Movie Ending", "stage": "STAGE.02",
                     "status": "idea", "priority": "med", "owner": "R.K", "due": "2026-09-22",
                     "sub": ["Script pass 1"]},
                    {"id": "c3", "asset": "YT-043", "title": "The Deer Hunt (99 Nights)", "stage": "HOOK",
                     "status": "idea", "priority": "high", "owner": "E.M", "due": "2026-09-20",
                     "sub": ["Waiting on thumbnail concept"]},
                ]},
                {"id": "production", "name": "In Production", "rows": [
                    {"id": "p1", "asset": "YT-039", "title": "Building the Impossible Base", "stage": "FILMING",
                     "status": "recorded", "priority": "high", "owner": "E.M", "due": "2026-09-16",
                     "sub": ["Record A-roll", "Record B-roll"]},
                    {"id": "p2", "asset": "YT-038", "title": "Every Cheat in Hide & Seek", "stage": "FIRST_CUT",
                     "status": "editing", "priority": "med", "owner": "R.K", "due": "2026-09-15",
                     "sub": ["Rough cut", "Music pass"]},
                ]},
                {"id": "published", "name": "Published", "rows": [
                    {"id": "p3", "asset": "YT-036", "title": "I Controlled Gravity in Gorilla Tag", "stage": "LIVE",
                     "status": "live", "priority": "low", "owner": "E.M", "due": "2026-09-10",
                     "sub": ["A / B / C variants shipped"]},
                ]},
            ],
        }

    def _ledger_migrate(led):
        """Upgrade an already-saved ledger in place: rename the stages, guarantee the third
        (Published) stage exists, drop the removed metric field. Idempotent."""
        changed = False
        names = {"concept": "Ideas", "production": "In Production", "published": "Published"}
        groups = led.get("groups") or []
        for g in groups:
            if not isinstance(g, dict):
                continue
            gid = g.get("id")
            if gid in names and g.get("name") != names[gid]:
                g["name"] = names[gid]; changed = True
            for r in (g.get("rows") or []):
                if not isinstance(r, dict):
                    continue
                if "metric" in r:
                    r.pop("metric", None); changed = True
                # migrate the old active/halt/done statuses to the new pipeline stages
                st = r.get("status")
                if st in ("active", "halt", "done"):
                    r["status"] = ("live" if st == "done" else "revisions" if st == "halt"
                                   else {"production": "editing", "published": "live"}.get(gid, "idea"))
                    changed = True
        if not any(isinstance(g, dict) and g.get("id") == "published" for g in groups):
            groups.append({"id": "published", "name": "Published", "rows": []}); changed = True
        led["groups"] = groups
        return led, changed

    @app.get("/api/ledger")
    def ledger_get():
        led = store.get_setting("ledger")
        if not isinstance(led, dict) or not isinstance(led.get("groups"), list):
            led = _ledger_seed()
            store.set_setting("ledger", led)
        else:
            led, changed = _ledger_migrate(led)
            if changed:
                store.set_setting("ledger", led)
        return jsonify({"ok": True, "ledger": led})

    @app.post("/api/ledger")
    def ledger_save():
        b = request.get_json(silent=True) or {}
        led = b.get("ledger")
        if not isinstance(led, dict) or not isinstance(led.get("groups"), list):
            return jsonify({"ok": False, "error": "ledger.groups required"}), 400
        if len(led["groups"]) > 40 or sum(len(g.get("rows", [])) for g in led["groups"] if isinstance(g, dict)) > 2000:
            return jsonify({"ok": False, "error": "ledger too large"}), 400
        store.set_setting("ledger", led)
        return jsonify({"ok": True})

    # ----------------------------------------------------------- extension
    @app.get("/api/ext/ping")
    def ext_ping():
        return jsonify({"ok": True, "app": config.APP_NAME, "version": config.APP_VERSION,
                        "channels": len(store.channels())})

    @app.get("/api/ext/channels")
    def ext_channels():
        handles = [h for h in (request.args.get("handles") or "").split(",") if h.strip()][:50]
        ids = [i for i in (request.args.get("ids") or "").split(",") if i.strip()][:50]
        out = {}
        need_h, need_i = [], []
        for h in handles:
            hit = store.cached_handle(h)
            if hit:
                out[h] = hit
            else:
                need_h.append(h)
        for i in ids:
            hit = store.cached_handle(i)
            if hit:
                out[i] = hit
            else:
                need_i.append(i)
        yt = public_client() if (need_h or need_i) else None
        if yt:
            for h in need_h:
                try:
                    c = yt.channel_by_handle(h)
                except http.HttpError:
                    c = None
                data = _ext_channel(c) if c else {"missing": True}
                store.cache_handle(h, data)
                out[h] = data
            if need_i:
                try:
                    found = {c["channel_id"]: c for c in yt.channels_by_ids(need_i)}
                except http.HttpError:
                    found = {}
                for i in need_i:
                    data = _ext_channel(found[i]) if i in found else {"missing": True}
                    store.cache_handle(i, data)
                    out[i] = data
        return jsonify({"channels": out})

    def _ext_channel(c):
        return {"id": c["channel_id"], "title": c["title"], "handle": c.get("handle"), "subscribers": c.get("subscribers"),
                "views": c.get("views"), "videos": c.get("videos"), "thumb": c.get("thumb")}

    @app.post("/api/ext/baselines")
    def ext_baselines():
        """Per-channel outlier baselines (median views) for the extension's on-YouTube badges.
        Body: {"channels": ["UC..." | "@handle", ...]}. Cached 24h; tracked channels are free."""
        import sf.search as search_mod
        body = request.get_json(silent=True) or {}
        idents = body.get("channels") or []
        if not isinstance(idents, list):
            return jsonify({"ok": False, "error": "channels must be a list"}), 400
        idents = [str(x).strip() for x in idents if str(x).strip()][:40]
        yt = public_client()
        out = {}
        for ident in idents:
            try:
                out[ident] = search_mod.channel_baseline(store, yt, ident)
            except Exception:
                out[ident] = {"error": True}
        return jsonify({"baselines": out})

    @app.post("/api/dev/markup")
    def dev_markup():
        """The extension's ad tools copy a Studio control's HTML when they can't act on it, and
        tell the user to 'paste it to CreatorHaven'. This is that paste target — it saves the
        markup to data/ext_markup.txt so the exact selectors can be wired."""
        body = request.get_json(silent=True) or {}
        m = str(body.get("markup") or "")[:200000]
        label = str(body.get("label") or "control")[:60]
        try:
            (config.DATA / "ext_markup.txt").write_text(f"# {label}\n{m}\n", encoding="utf-8")
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, "len": len(m)})

    @app.post("/api/ext/realtime")
    def ext_realtime():
        body = request.get_json(silent=True) or {}
        cid = body.get("channelId") or body.get("channel_id") or "unknown"
        samples = body.get("samples") or []
        if not isinstance(samples, list):
            return jsonify({"ok": False, "error": "samples must be a list"}), 400
        store.set_realtime(cid, samples[:100], body.get("url") or "")
        return jsonify({"ok": True, "stored": len(samples[:100])})

    @app.post("/api/ext/analytics")
    def ext_analytics():
        """The extension pushes per-channel analytics it read from a logged-in Studio
        session (delegated channels the official API cannot reach). Written into the
        SAME tables the dashboard reads, with refresh_token=None so the API sync skips
        them and a scopes string that clears the 'no analytics scope' banner."""
        body = request.get_json(silent=True) or {}
        chans = body.get("channels")
        if isinstance(body.get("channel"), dict):
            chans = [body["channel"]]
        if not isinstance(chans, list) or not chans:
            return jsonify({"ok": False, "error": "channels must be a non-empty list"}), 400
        done = []
        for c in chans[:200]:
            cid = c.get("channel_id") or c.get("channelId") or c.get("id")
            if not cid:
                continue
            existing = store.channel(cid) or {}
            # partial pushes (e.g. a videos-only harvest) must not wipe identity/scopes
            if "scopes" in c:
                sc = c.get("scopes") or {}
                parts = [oauth.SCOPE_READONLY]
                if sc.get("analytics", True):
                    parts.append(oauth.SCOPE_ANALYTICS)
                if sc.get("monetary"):
                    parts.append(oauth.SCOPE_MONETARY)
                if sc.get("edit"):
                    parts.append(oauth.SCOPE_FORCE_SSL)
                scopes = " ".join(parts)
            else:
                scopes = existing.get("scopes") or (oauth.SCOPE_READONLY + " " + oauth.SCOPE_ANALYTICS)
            store.upsert_channel({"channel_id": cid,
                                  "title": c.get("title") or existing.get("title"),
                                  "handle": c.get("handle") or existing.get("handle"),
                                  "thumb": c.get("thumb") or existing.get("thumb"),
                                  "uploads_playlist": c.get("uploads_playlist") or existing.get("uploads_playlist"),
                                  "refresh_token": None, "scopes": scopes})
            if isinstance(c.get("stats"), dict):
                store.set_channel_stats(cid, {k: c["stats"].get(k) for k in ("subscribers", "views", "videos")})
            if isinstance(c.get("videos"), list) and c["videos"]:
                store.upsert_videos(cid, c["videos"][:2000])
            if isinstance(c.get("daily"), list) and c["daily"]:
                store.upsert_daily(cid, c["daily"][:400])
            top = c.get("top") or c.get("top_videos")
            if isinstance(top, list) and top:
                store.upsert_video_stats(cid, int(c.get("window_days") or 28), top[:200])
            store.set_channel_sync(cid, error=None)
            done.append(cid)
        log(f"[ext] analytics push: {len(done)} channel(s) {', '.join(done[:6])}")
        return jsonify({"ok": True, "channels": done, "count": len(done)})

    @app.post("/api/ext/probe")
    def ext_probe():
        """Auto-capture of the extension's probe entries (full request/response for the
        few analytics endpoints) so Studio's real request shapes arrive without anyone
        exporting a log by hand. Appended to data/probe_capture.jsonl for the build."""
        body = request.get_json(silent=True) or {}
        entries = body.get("entries")
        if not isinstance(entries, list):
            return jsonify({"ok": False, "error": "entries must be a list"}), 400
        data_dir = os.path.dirname(os.path.abspath(cfg.get("database") or "data/x")) or "."
        path = os.path.join(data_dir, "probe_capture.jsonl")
        try:
            if os.path.exists(path) and os.path.getsize(path) > 8_000_000:
                open(path, "w", encoding="utf-8").close()
            with open(path, "a", encoding="utf-8") as f:
                for e in entries[:200]:
                    if isinstance(e, dict):
                        line = json.dumps(e, ensure_ascii=False)   # fields already capped ext-side; never slice valid JSON
                        if len(line) <= 700000:
                            f.write(line + "\n")
        except Exception as ex:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(ex)}), 500
        return jsonify({"ok": True, "stored": len(entries[:200])})

    return app


if __name__ == "__main__":
    cfg = config.load()
    application = create_app(cfg, start_background=True)
    print(f"{config.APP_NAME} v{config.APP_VERSION} on http://127.0.0.1:{cfg['port']}  "
          f"(login {'ON' if cfg['site_password'] else 'off'}, google client {'ok' if cfg['google']['client_id'] else 'MISSING'})")
    application.run(host="127.0.0.1", port=int(cfg["port"]), debug=False, threaded=True)
