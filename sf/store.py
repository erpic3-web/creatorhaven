"""SQLite storage (WAL, one process lock). Every table carries a user_id so the
single-operator install can become multi-user later without a migration of
meaning, only of rows. Refresh tokens are stored as-is for now; encrypting them
at rest is a listed pre-rollout task (README).
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS channels (
  channel_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL DEFAULT 1,
  title TEXT, handle TEXT, thumb TEXT, uploads_playlist TEXT,
  refresh_token TEXT, scopes TEXT, linked_at REAL, last_sync REAL,
  stats_json TEXT, sync_error TEXT);
CREATE TABLE IF NOT EXISTS videos (
  video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,
  title TEXT, description_len INTEGER, published_at TEXT, privacy TEXT, publish_at TEXT,
  upload_status TEXT, made_for_kids INTEGER, yt_rating TEXT, duration_s INTEGER, is_short INTEGER,
  views INTEGER, likes INTEGER, comments INTEGER, tags_json TEXT, thumb TEXT, live INTEGER,
  updated_at REAL);
CREATE INDEX IF NOT EXISTS videos_channel ON videos(channel_id, published_at);
CREATE TABLE IF NOT EXISTS daily (
  channel_id TEXT NOT NULL, day TEXT NOT NULL, views INTEGER, engaged_views INTEGER,
  minutes INTEGER, avg_view_s INTEGER, subs_gained INTEGER, subs_lost INTEGER,
  likes INTEGER, comments INTEGER, shares INTEGER, revenue REAL, fetched_at REAL,
  PRIMARY KEY (channel_id, day));
CREATE TABLE IF NOT EXISTS video_stats (
  channel_id TEXT NOT NULL, video_id TEXT NOT NULL, window_days INTEGER NOT NULL,
  views INTEGER, engaged_views INTEGER, minutes INTEGER, fetched_at REAL,
  PRIMARY KEY (channel_id, video_id, window_days));
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, key TEXT UNIQUE, user_id INTEGER NOT NULL DEFAULT 1,
  channel_id TEXT, video_id TEXT, kind TEXT, severity TEXT, title TEXT, message TEXT,
  link TEXT, created_at REAL, dismissed INTEGER DEFAULT 0, delivered INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS quota (day TEXT PRIMARY KEY, units INTEGER DEFAULT 0, calls INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS realtime (
  channel_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT, path TEXT, url TEXT, captured_at REAL,
  PRIMARY KEY (channel_id, key));
CREATE TABLE IF NOT EXISTS handle_cache (handle TEXT PRIMARY KEY, json TEXT, fetched_at REAL);
"""

VIDEO_COLS = ["video_id", "channel_id", "title", "description_len", "published_at", "privacy",
              "publish_at", "upload_status", "made_for_kids", "yt_rating", "duration_s", "is_short",
              "views", "likes", "comments", "tags_json", "thumb", "live", "updated_at"]


def _row(cur, r):
    return {d[0]: r[i] for i, d in enumerate(cur.description)} if r else None


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._db.executescript(SCHEMA)
        self.ensure_owner()

    # ---------------------------------------------------------------- basics
    def _exec(self, sql, params=()):
        with self._lock:
            return self._db.execute(sql, params)

    def _all(self, sql, params=()):
        with self._lock:
            cur = self._db.execute(sql, params)
            return [_row(cur, r) for r in cur.fetchall()]

    def _one(self, sql, params=()):
        with self._lock:
            cur = self._db.execute(sql, params)
            return _row(cur, cur.fetchone())

    def close(self):
        with self._lock:
            self._db.close()

    # ----------------------------------------------------------------- users
    def ensure_owner(self):
        if not self._one("SELECT id FROM users WHERE id=1"):
            self._exec("INSERT INTO users(id,email,name,created_at) VALUES(1,'owner','Owner',?)",
                       (time.time(),))
        return 1

    # -------------------------------------------------------------- channels
    def upsert_channel(self, ch, user_id=1):
        old = self.channel(ch["channel_id"])
        self._exec(
            """INSERT INTO channels(channel_id,user_id,title,handle,thumb,uploads_playlist,refresh_token,
                                    scopes,linked_at,last_sync,stats_json,sync_error)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title, handle=excluded.handle,
                 thumb=excluded.thumb, uploads_playlist=excluded.uploads_playlist,
                 refresh_token=COALESCE(excluded.refresh_token, channels.refresh_token),
                 scopes=excluded.scopes, linked_at=excluded.linked_at""",
            (ch["channel_id"], user_id, ch.get("title"), ch.get("handle"), ch.get("thumb"),
             ch.get("uploads_playlist"), ch.get("refresh_token"), ch.get("scopes", ""),
             ch.get("linked_at", time.time()), old["last_sync"] if old else None,
             old["stats_json"] if old else None, None))
        return self.channel(ch["channel_id"])

    def channels(self, user_id=1):
        rows = self._all("SELECT * FROM channels WHERE user_id=? ORDER BY title COLLATE NOCASE", (user_id,))
        for r in rows:
            r["stats"] = json.loads(r.pop("stats_json") or "{}")
            r["has_token"] = bool(r.pop("refresh_token"))
        return rows

    def channel(self, channel_id):
        return self._one("SELECT * FROM channels WHERE channel_id=?", (channel_id,))

    def delete_channel(self, channel_id):
        for t in ("videos", "daily", "video_stats", "realtime"):
            self._exec(f"DELETE FROM {t} WHERE channel_id=?", (channel_id,))
        self._exec("DELETE FROM alerts WHERE channel_id=?", (channel_id,))
        self._exec("DELETE FROM channels WHERE channel_id=?", (channel_id,))

    def set_channel_stats(self, channel_id, stats):
        self._exec("UPDATE channels SET stats_json=? WHERE channel_id=?", (json.dumps(stats), channel_id))

    def set_channel_sync(self, channel_id, ts=None, error=None):
        self._exec("UPDATE channels SET last_sync=?, sync_error=? WHERE channel_id=?",
                   (ts if ts is not None else time.time(), error, channel_id))

    def set_channel_scopes(self, channel_id, scopes):
        self._exec("UPDATE channels SET scopes=? WHERE channel_id=?", (scopes, channel_id))

    # ---------------------------------------------------------------- videos
    def upsert_videos(self, channel_id, videos):
        now = time.time()
        with self._lock:
            for v in videos:
                row = dict(v)
                row["channel_id"] = channel_id
                row["updated_at"] = now
                row["tags_json"] = json.dumps(row.pop("tags", []) or [])
                for c in VIDEO_COLS:
                    row.setdefault(c, None)
                cols = ",".join(VIDEO_COLS)
                marks = ",".join("?" * len(VIDEO_COLS))
                upd = ",".join(f"{c}=excluded.{c}" for c in VIDEO_COLS if c != "video_id")
                self._db.execute(f"INSERT INTO videos({cols}) VALUES({marks}) "
                                 f"ON CONFLICT(video_id) DO UPDATE SET {upd}",
                                 tuple(row[c] for c in VIDEO_COLS))

    def videos(self, channel_id=None, limit=None, user_id=1):
        if channel_id:
            sql, params = "SELECT * FROM videos WHERE channel_id=? ORDER BY published_at DESC", (channel_id,)
        else:
            sql = ("SELECT v.* FROM videos v JOIN channels c ON c.channel_id=v.channel_id "
                   "WHERE c.user_id=? ORDER BY v.published_at DESC")
            params = (user_id,)
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self._all(sql, params)
        for r in rows:
            r["tags"] = json.loads(r.pop("tags_json") or "[]")
        return rows

    def video(self, video_id):
        r = self._one("SELECT * FROM videos WHERE video_id=?", (video_id,))
        if r:
            r["tags"] = json.loads(r.pop("tags_json") or "[]")
        return r

    # ----------------------------------------------------------------- daily
    def upsert_daily(self, channel_id, rows):
        now = time.time()
        with self._lock:
            for r in rows:
                self._db.execute(
                    """INSERT INTO daily(channel_id,day,views,engaged_views,minutes,avg_view_s,subs_gained,
                                        subs_lost,likes,comments,shares,revenue,fetched_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(channel_id,day) DO UPDATE SET views=excluded.views,
                         engaged_views=COALESCE(excluded.engaged_views, daily.engaged_views),
                         minutes=excluded.minutes, avg_view_s=excluded.avg_view_s,
                         subs_gained=excluded.subs_gained, subs_lost=excluded.subs_lost,
                         likes=excluded.likes, comments=excluded.comments, shares=excluded.shares,
                         revenue=COALESCE(excluded.revenue, daily.revenue), fetched_at=excluded.fetched_at""",
                    (channel_id, r["day"], r.get("views"), r.get("engaged_views"), r.get("minutes"),
                     r.get("avg_view_s"), r.get("subs_gained"), r.get("subs_lost"), r.get("likes"),
                     r.get("comments"), r.get("shares"), r.get("revenue"), now))

    def daily(self, channel_id, start_day, end_day):
        return self._all("SELECT * FROM daily WHERE channel_id=? AND day>=? AND day<=? ORDER BY day",
                         (channel_id, start_day, end_day))

    def daily_all(self, start_day, end_day, user_id=1):
        return self._all(
            "SELECT d.* FROM daily d JOIN channels c ON c.channel_id=d.channel_id "
            "WHERE c.user_id=? AND d.day>=? AND d.day<=? ORDER BY d.day", (user_id, start_day, end_day))

    def upsert_video_stats(self, channel_id, window_days, rows):
        now = time.time()
        with self._lock:
            for r in rows:
                self._db.execute(
                    """INSERT INTO video_stats(channel_id,video_id,window_days,views,engaged_views,minutes,fetched_at)
                       VALUES(?,?,?,?,?,?,?) ON CONFLICT(channel_id,video_id,window_days) DO UPDATE SET
                       views=excluded.views, engaged_views=excluded.engaged_views, minutes=excluded.minutes,
                       fetched_at=excluded.fetched_at""",
                    (channel_id, r["video_id"], window_days, r.get("views"), r.get("engaged_views"),
                     r.get("minutes"), now))

    def video_stats(self, channel_id, window_days):
        return self._all("SELECT * FROM video_stats WHERE channel_id=? AND window_days=? ORDER BY views DESC",
                         (channel_id, window_days))

    # ---------------------------------------------------------------- alerts
    def add_alert(self, a, user_id=1):
        """Insert unless an alert with the same key exists. Returns True when new."""
        if self._one("SELECT id FROM alerts WHERE key=?", (a["key"],)):
            return False
        self._exec(
            """INSERT INTO alerts(key,user_id,channel_id,video_id,kind,severity,title,message,link,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (a["key"], user_id, a.get("channel_id"), a.get("video_id"), a.get("kind"),
             a.get("severity", "info"), a.get("title"), a.get("message"), a.get("link"),
             a.get("created_at", time.time())))
        return True

    def alerts(self, include_dismissed=False, limit=200, user_id=1):
        sql = "SELECT * FROM alerts WHERE user_id=?" + ("" if include_dismissed else " AND dismissed=0")
        return self._all(sql + f" ORDER BY created_at DESC LIMIT {int(limit)}", (user_id,))

    def undelivered_alerts(self, user_id=1):
        return self._all("SELECT * FROM alerts WHERE user_id=? AND delivered=0 ORDER BY created_at", (user_id,))

    def mark_delivered(self, ids):
        with self._lock:
            for i in ids:
                self._db.execute("UPDATE alerts SET delivered=1 WHERE id=?", (i,))

    def dismiss_alert(self, alert_id):
        self._exec("UPDATE alerts SET dismissed=1 WHERE id=?", (alert_id,))

    # -------------------------------------------------------------- settings
    def get_setting(self, key, default=None):
        r = self._one("SELECT value FROM settings WHERE key=?", (key,))
        if not r:
            return default
        try:
            return json.loads(r["value"])
        except Exception:
            return r["value"]

    def set_setting(self, key, value):
        self._exec("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value)))

    # ----------------------------------------------------------------- quota
    def add_quota(self, day, units):
        self._exec("INSERT INTO quota(day,units,calls) VALUES(?,?,1) ON CONFLICT(day) DO UPDATE SET "
                   "units=quota.units+excluded.units, calls=quota.calls+1", (day, units))

    def quota(self, day):
        return self._one("SELECT * FROM quota WHERE day=?", (day,)) or {"day": day, "units": 0, "calls": 0}

    # -------------------------------------------------------------- realtime
    def set_realtime(self, channel_id, samples, url=""):
        now = time.time()
        with self._lock:
            for s in samples:
                self._db.execute(
                    "INSERT INTO realtime(channel_id,key,value,path,url,captured_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(channel_id,key) DO UPDATE SET value=excluded.value, path=excluded.path, "
                    "url=excluded.url, captured_at=excluded.captured_at",
                    (channel_id, str(s.get("key"))[:120], json.dumps(s.get("value")), str(s.get("path"))[:300],
                     url[:200], now))

    def realtime(self, channel_id):
        rows = self._all("SELECT * FROM realtime WHERE channel_id=? ORDER BY key", (channel_id,))
        for r in rows:
            try:
                r["value"] = json.loads(r["value"])
            except Exception:
                pass
        return rows

    # ---------------------------------------------------------- handle cache
    def cached_handle(self, handle, max_age=7 * 86400):
        r = self._one("SELECT * FROM handle_cache WHERE handle=?", (handle.lower(),))
        if r and time.time() - r["fetched_at"] < max_age:
            return json.loads(r["json"])
        return None

    def cache_handle(self, handle, data):
        self._exec("INSERT INTO handle_cache(handle,json,fetched_at) VALUES(?,?,?) ON CONFLICT(handle) DO UPDATE SET "
                   "json=excluded.json, fetched_at=excluded.fetched_at", (handle.lower(), json.dumps(data), time.time()))
