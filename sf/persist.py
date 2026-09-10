"""Durable storage for hosted deploys (Render).

The site keeps everything in ONE SQLite file (`Store`). A Render web service has an
ephemeral filesystem, so without help every deploy or restart would wipe the linked
channels, tokens, radar cache, ledger and chat. Rather than port 300 lines of SQL to
Postgres, this module keeps the SQLite file itself durable: when `DATABASE_URL` is set
(a Render Postgres, free tier is fine) the file is restored from a single-row `bytea`
table at boot and pushed back after every write-heavy action plus on a timer.

Zero cost when DATABASE_URL is empty (local use) — `enabled()` is False and every call
is a no-op. psycopg (v3) is only imported when a URL is present.
"""
import os
import sqlite3
import threading
import time
from pathlib import Path

TABLE = "sf_sqlite_backup"
KEY = "studioforge"
_lock = threading.Lock()
_state = {"url": None, "last_push": 0.0, "last_error": "", "pushes": 0, "restored": None, "size": 0}


def enabled():
    return bool(_state["url"])


def configure(url):
    _state["url"] = (url or "").strip() or None
    return enabled()


def _connect():
    import psycopg  # noqa: WPS433  (optional dependency, hosted only)
    return psycopg.connect(_state["url"], connect_timeout=15)


def _ensure(conn):
    conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (k TEXT PRIMARY KEY, data BYTEA NOT NULL, "
                 f"size INTEGER NOT NULL, updated_at DOUBLE PRECISION NOT NULL)")


def restore(path):
    """Pull the newest backup over `path` before the Store opens it. Returns True if a
    backup was applied, False if none exists (first boot) or persistence is off."""
    if not enabled():
        return False
    try:
        with _connect() as conn:
            _ensure(conn)
            row = conn.execute(f"SELECT data, size, updated_at FROM {TABLE} WHERE k = %s", (KEY,)).fetchone()
        if not row:
            _state["restored"] = "none"
            return False
        data = bytes(row[0])
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".restore")
        tmp.write_bytes(data)
        # never trust a blob blindly: it must open as a database before it replaces anything
        chk = sqlite3.connect(str(tmp))
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        chk.close()
        if not ok:
            tmp.unlink(missing_ok=True)
            _state["last_error"] = "backup failed integrity_check; starting fresh"
            _state["restored"] = "corrupt"
            return False
        for suffix in ("", "-wal", "-shm"):
            Path(str(p) + suffix).unlink(missing_ok=True)
        tmp.replace(p)
        _state["restored"] = f"{len(data)} bytes from {time.strftime('%Y-%m-%d %H:%M', time.gmtime(row[2]))} UTC"
        _state["size"] = len(data)
        return True
    except Exception as e:  # noqa: BLE001
        _state["last_error"] = f"restore: {type(e).__name__}: {e}"
        _state["restored"] = "error"
        return False


def push(store):
    """Write a consistent snapshot of the live database into Postgres."""
    if not enabled():
        return False
    with _lock:
        try:
            snap = sqlite3.connect(":memory:")
            with store._lock:
                store._db.backup(snap)          # the online-backup API: consistent under WAL
            data = b"".join(_serialize(snap))
            snap.close()
            with _connect() as conn:
                _ensure(conn)
                conn.execute(
                    f"INSERT INTO {TABLE} (k, data, size, updated_at) VALUES (%s, %s, %s, %s) "
                    f"ON CONFLICT (k) DO UPDATE SET data = EXCLUDED.data, size = EXCLUDED.size, updated_at = EXCLUDED.updated_at",
                    (KEY, data, len(data), time.time()))
            _state.update(last_push=time.time(), pushes=_state["pushes"] + 1, last_error="", size=len(data))
            return True
        except Exception as e:  # noqa: BLE001
            _state["last_error"] = f"push: {type(e).__name__}: {e}"
            return False


def _serialize(conn):
    """Bytes of an in-memory SQLite database (Python 3.11+ has Connection.serialize)."""
    if hasattr(conn, "serialize"):
        yield conn.serialize()
        return
    # fallback: dump to a temp file
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    disk = sqlite3.connect(tmp)
    conn.backup(disk)
    disk.close()
    yield Path(tmp).read_bytes()
    os.unlink(tmp)


def start_timer(store, minutes=10):
    """Background pusher: every N minutes, plus a best-effort push at interpreter exit."""
    if not enabled():
        return None

    def loop():
        while True:
            time.sleep(max(60, int(minutes) * 60))
            push(store)

    t = threading.Thread(target=loop, daemon=True, name="sf-persist")
    t.start()
    import atexit
    atexit.register(lambda: push(store))
    return t


def status():
    return {
        "enabled": enabled(),
        "restored": _state["restored"],
        "last_push": _state["last_push"],
        "pushes": _state["pushes"],
        "size": _state["size"],
        "error": _state["last_error"],
    }
