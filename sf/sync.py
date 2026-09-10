"""The sync pipeline: refresh tokens, pull channel stats + newest uploads with
the Data API, daily series + top videos with the Analytics API when the
channel's scopes allow it, then run the alert rules. Also the one-time import
of channels already linked by yt-schedule-bot.
"""
import json
import time
from datetime import date, timedelta
from pathlib import Path

from . import alerts as alerts_mod
from . import api_analytics, api_youtube, config, http, metrics, oauth


class TokenCache:
    """Access tokens per channel with expiry; refreshes through the OAuth client."""

    def __init__(self, cfg, token_base=oauth.TOKEN_BASE):
        self.cfg = cfg
        self.token_base = token_base
        self._cache = {}

    def get(self, channel):
        cid = channel["channel_id"]
        hit = self._cache.get(cid)
        if hit and hit[1] > time.time() + 60:
            return hit[0]
        if not channel.get("refresh_token"):
            raise RuntimeError("channel has no refresh token; re-link it")
        access, ttl, granted = oauth.refresh_access_token(self.cfg, channel["refresh_token"], self.token_base)
        self._cache[cid] = (access, time.time() + ttl, granted)
        return access

    def granted_scopes(self, channel):
        self.get(channel)
        return self._cache[channel["channel_id"]][2]

    def invalidate(self, channel_id):
        self._cache.pop(channel_id, None)


def today():
    return date.today().isoformat()


def quota_cb(store):
    def cb(units):
        store.add_quota(today(), units)
    return cb


def link_channel(store, cfg, token_response, user_id=1, api_base=api_youtube.API_BASE):
    """Finish an OAuth link: fetch the channel for this login and persist it."""
    access = token_response["access_token"]
    yt = api_youtube.YouTube(access_token=access, quota_cb=quota_cb(store), base=api_base)
    ch = yt.channel_mine()
    row = {"channel_id": ch["channel_id"], "title": ch["title"], "handle": ch.get("handle"), "thumb": ch.get("thumb"),
           "uploads_playlist": ch.get("uploads_playlist"), "refresh_token": token_response.get("refresh_token"),
           "scopes": token_response.get("scope", ""), "linked_at": time.time()}
    existing = store.channel(ch["channel_id"])
    if not row["refresh_token"] and not (existing and existing.get("refresh_token")):
        raise RuntimeError("Google returned no refresh token — remove the app at myaccount.google.com/permissions and link again")
    store.upsert_channel(row, user_id)
    store.set_channel_stats(ch["channel_id"], {k: ch.get(k) for k in ("subscribers", "views", "videos", "hidden_subs", "country", "published_at", "banner")})
    return store.channel(ch["channel_id"])


def import_schedule_bot(store, cfg, user_id=1, bot_dir=None):
    """Import channels the yt-schedule-bot linked (same OAuth client, youtube.readonly scope)."""
    bot_dir = Path(bot_dir or config.SCHEDULE_BOT)
    tokens_dir = bot_dir / "data" / "tokens"
    imported = []
    if not tokens_dir.exists():
        return imported
    for f in sorted(tokens_dir.glob("*.json")):
        try:
            t = json.loads(f.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not t.get("channelId") or not t.get("refreshToken"):
            continue
        if store.channel(t["channelId"]) and store.channel(t["channelId"]).get("refresh_token"):
            continue
        store.upsert_channel({"channel_id": t["channelId"], "title": t.get("title"), "handle": None, "thumb": None,
                              "uploads_playlist": t.get("uploadsPlaylistId"), "refresh_token": t["refreshToken"],
                              "scopes": oauth.SCOPE_READONLY, "linked_at": time.time()}, user_id)
        imported.append(t.get("title") or t["channelId"])
    return imported


def sync_channel(store, cfg, channel, tokens, api_base=api_youtube.API_BASE,
                 analytics_base=api_analytics.ANALYTICS_BASE, log=print):
    """Full refresh of one channel. Returns a summary dict; never raises for API trouble."""
    cid = channel["channel_id"]
    summary = {"channel_id": cid, "title": channel.get("title"), "videos": 0, "daily": 0, "alerts": 0,
               "analytics": None, "errors": []}
    try:
        access = tokens.get(channel)
        granted = tokens.granted_scopes(channel)
        if granted and granted != channel.get("scopes"):
            store.set_channel_scopes(cid, granted)
            channel = dict(channel, scopes=granted)
    except Exception as e:
        # dead / revoked token -> fall back to PUBLIC data so the channel still fills
        pub = public_client(store, cfg, tokens)
        if pub:
            log(f"[sync] {channel.get('title') or cid}: token dead, using public data")
            return sync_channel_public(store, cfg, channel, pub, log=log)
        msg = f"token refresh failed: {e}"
        store.set_channel_sync(cid, error=msg)
        summary["errors"].append(msg)
        return summary

    yt = api_youtube.YouTube(access_token=access, quota_cb=quota_cb(store), base=api_base)
    try:
        ch = yt.channel_mine()
        store.upsert_channel({"channel_id": cid, "title": ch["title"], "handle": ch.get("handle"), "thumb": ch.get("thumb"),
                              "uploads_playlist": ch.get("uploads_playlist"), "refresh_token": None,
                              "scopes": channel.get("scopes", ""), "linked_at": channel.get("linked_at") or time.time()})
        store.set_channel_stats(cid, {k: ch.get(k) for k in ("subscribers", "views", "videos", "hidden_subs", "country", "published_at", "banner")})
        ids = yt.playlist_video_ids(ch["uploads_playlist"], limit=int(cfg.get("scan_per_channel") or 200))
        vids = yt.videos(ids)
        store.upsert_videos(cid, vids)
        summary["videos"] = len(vids)
    except Exception as e:
        msg = f"Data API: {getattr(e, 'message', None) or e}"
        summary["errors"].append(msg)
        store.set_channel_sync(cid, error=msg)
        log(f"[sync] {channel.get('title')}: {msg}")
        return summary

    scopes = channel.get("scopes") or ""
    if oauth.has_scope(scopes, oauth.SCOPE_ANALYTICS):
        an = api_analytics.Analytics(access, base=analytics_base)
        monetary = oauth.has_scope(scopes, oauth.SCOPE_MONETARY)
        try:
            rows = an.daily(days=int(cfg.get("analytics_days") or 90), monetary=monetary)
            store.upsert_daily(cid, rows)
            summary["daily"] = len(rows)
            top = an.top_videos(days=28)
            store.upsert_video_stats(cid, 28, top)
            summary["analytics"] = "ok"
        except api_analytics.NeedsScope as e:
            summary["analytics"] = "needs_scope"
            summary["errors"].append(f"Analytics: re-link with the analytics scope ({e})")
        except api_analytics.ApiNotEnabled as e:
            summary["analytics"] = "api_disabled"
            summary["errors"].append("Analytics: enable the YouTube Analytics API in the Google Cloud project")
        except http.HttpError as e:
            summary["analytics"] = "error"
            summary["errors"].append(f"Analytics: {e.message or e}")
    else:
        summary["analytics"] = "needs_scope"

    try:
        fresh = store.channel(cid)
        fresh["stats"] = json.loads(fresh.get("stats_json") or "{}")
        new_alerts = alerts_mod.run_scan(store, fresh, store.videos(cid))
        summary["alerts"] = len(new_alerts)
    except Exception as e:
        summary["errors"].append(f"alerts: {e}")

    store.set_channel_sync(cid, error=("; ".join(summary["errors"]) or None))
    return summary


def sync_channel_public(store, cfg, channel, pub, log=print):
    """Refresh a NO-TOKEN channel from the PUBLIC Data API (any linked channel's token or an
    API key works — public reads see any channel). Fills stats + videos (subs, views, per-video
    views/likes/comments, outliers, content table); no analytics (engaged/daily/revenue are
    not public — those need OAuth on an owned channel or the extension on a delegated one)."""
    cid = channel["channel_id"]
    summary = {"channel_id": cid, "title": channel.get("title"), "videos": 0, "daily": 0,
               "analytics": "public", "errors": []}
    if not pub:
        summary["errors"].append("no public client (link one channel or set a YouTube API key)")
        store.set_channel_sync(cid, error=summary["errors"][-1])
        return summary
    try:
        found = pub.channels_by_ids([cid])
        ch = found[0] if found else None
        if not ch:
            summary["errors"].append("channel not found via public API")
            store.set_channel_sync(cid, error=summary["errors"][-1])
            return summary
        store.upsert_channel({"channel_id": cid, "title": ch["title"], "handle": ch.get("handle"),
                              "thumb": ch.get("thumb"), "uploads_playlist": ch.get("uploads_playlist"),
                              "refresh_token": None, "scopes": channel.get("scopes") or oauth.SCOPE_READONLY,
                              "linked_at": channel.get("linked_at") or time.time()})
        store.set_channel_stats(cid, {k: ch.get(k) for k in
                                      ("subscribers", "views", "videos", "hidden_subs", "country", "published_at", "banner")})
        try:  # daily growth history for the search/lookup page (public channels have no Analytics API)
            from . import search as _search
            _search.record_snapshot(store, cid, ch)
        except Exception:
            pass
        summary["title"] = ch["title"]
        if ch.get("uploads_playlist"):
            ids = pub.playlist_video_ids(ch["uploads_playlist"], limit=int(cfg.get("scan_per_channel") or 200))
            vids = pub.videos(ids)
            store.upsert_videos(cid, vids)
            summary["videos"] = len(vids)
    except Exception as e:  # noqa: BLE001
        msg = f"Data API: {getattr(e, 'message', None) or e}"
        summary["errors"].append(msg)
        store.set_channel_sync(cid, error=msg)
        log(f"[sync] {channel.get('title') or cid}: {msg}")
        return summary
    try:
        fresh = store.channel(cid)
        fresh["stats"] = json.loads(fresh.get("stats_json") or "{}")
        summary["alerts"] = len(alerts_mod.run_scan(store, fresh, store.videos(cid)))
    except Exception as e:  # noqa: BLE001
        summary["errors"].append(f"alerts: {e}")
    store.set_channel_sync(cid, error=("; ".join(summary["errors"]) or None))
    return summary


def sync_all(store, cfg, tokens=None, log=print, **bases):
    tokens = tokens or TokenCache(cfg)
    pub = public_client(store, cfg, tokens)
    out = []
    for ch in store._all("SELECT * FROM channels ORDER BY title COLLATE NOCASE"):
        if ch.get("refresh_token"):
            out.append(sync_channel(store, cfg, ch, tokens, log=log, **bases))
        else:
            out.append(sync_channel_public(store, cfg, ch, pub, log=log))
    if cfg.get("discord_webhook_url"):
        try:
            alerts_mod.deliver_discord(store, cfg["discord_webhook_url"])
        except Exception as e:
            log(f"[sync] discord delivery failed: {e}")
    store.set_setting("last_sync", {"at": time.time(), "summary": out})
    return out


def any_access_token(store, cfg, tokens):
    """An access token from any linked channel, for public Data API reads by the tools."""
    for ch in store._all("SELECT * FROM channels WHERE refresh_token IS NOT NULL ORDER BY last_sync DESC"):
        try:
            return tokens.get(ch)
        except Exception:
            continue
    return None


def public_client(store, cfg, tokens):
    if cfg.get("youtube_api_key"):
        return api_youtube.YouTube(api_key=cfg["youtube_api_key"], quota_cb=quota_cb(store))
    tok = any_access_token(store, cfg, tokens)
    if tok:
        return api_youtube.YouTube(access_token=tok, quota_cb=quota_cb(store))
    return None


def add_public_channels(store, cfg, refs, log=print):
    """Resolve handles / URLs / UC-ids and add+sync each as a PUBLIC channel (no OAuth).
    One paste of the whole roster, then the background sync keeps them fresh — the operator
    never goes channel-by-channel."""
    import re
    tokens = TokenCache(cfg)
    pub = public_client(store, cfg, tokens)
    if not pub:
        return {"error": "No public client yet — link one channel (for its token) or set a YouTube API key in Settings."}
    added, failed = [], []
    for ref in refs:
        ref = (ref or "").strip()
        if not ref:
            continue
        try:
            m = re.search(r'(UC[0-9A-Za-z_-]{20,})', ref)
            if m:
                cid = m.group(1)
            else:
                mh = re.search(r'@[A-Za-z0-9._-]+', ref)
                handle = mh.group(0) if mh else ("@" + ref.lstrip("@"))
                ch = pub.channel_by_handle(handle)
                cid = ch.get("channel_id") if ch else None
            if not cid:
                failed.append(ref)
                continue
            if not store.channel(cid):
                store.upsert_channel({"channel_id": cid, "refresh_token": None, "scopes": oauth.SCOPE_READONLY})
            r = sync_channel_public(store, cfg, store.channel(cid), pub, log=log)
            added.append({"ref": ref, "channel_id": cid, "title": r.get("title"),
                          "videos": r.get("videos"), "error": ("; ".join(r["errors"]) or None)})
        except Exception as e:  # noqa: BLE001
            failed.append(f"{ref}: {getattr(e, 'message', None) or e}")
    return {"added": added, "failed": failed}


def channel_window(store, channel_id, days):
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return store.daily(channel_id, start.isoformat(), end.isoformat())


def overview(store, cfg, days=28, user_id=1):
    channels = store.channels(user_id)
    rows_by = {c["channel_id"]: channel_window(store, c["channel_id"], days) for c in channels}
    combined = metrics.combine_daily(rows_by)
    per = []
    for c in channels:
        tot = metrics.sum_totals(rows_by[c["channel_id"]])
        per.append({"channel_id": c["channel_id"], "title": c["title"], "thumb": c["thumb"], "stats": c["stats"],
                    "totals": tot, "last_sync": c["last_sync"], "sync_error": c["sync_error"],
                    "analytics": bool(rows_by[c["channel_id"]]),
                    "scopes": {"analytics": oauth.has_scope(c.get("scopes"), oauth.SCOPE_ANALYTICS),
                               "monetary": oauth.has_scope(c.get("scopes"), oauth.SCOPE_MONETARY),
                               "edit": oauth.has_scope(c.get("scopes"), oauth.SCOPE_FORCE_SSL)}})
    return {"days": days, "channels": per, "combined": metrics.combined_channel_stats(channels),
            "series": combined, "totals": metrics.sum_totals(combined)}
