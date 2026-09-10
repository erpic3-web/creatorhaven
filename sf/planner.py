"""Personal content calendar. Public channels have no readable 'scheduled uploads', so
this calendar is the CREATOR'S OWN posting plan: they drop planned videos onto days and
get reminded (in-app alert + Discord) when one is due. One plan per operator (user_id)."""
import time
from datetime import date

from . import alerts as alerts_mod

FIELDS = ("id", "user_id", "day", "title", "channel_id", "note", "status", "notify", "alerted", "created_at")


def _ensure(store):
    store._exec("""CREATE TABLE IF NOT EXISTS content_plan (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL DEFAULT 1, day TEXT NOT NULL,
        title TEXT, channel_id TEXT, note TEXT, status TEXT DEFAULT 'planned',
        notify INTEGER DEFAULT 1, alerted INTEGER DEFAULT 0, created_at REAL)""")


def add(store, day, title, channel_id=None, note="", notify=True, user_id=1):
    _ensure(store)
    store._exec("INSERT INTO content_plan(user_id,day,title,channel_id,note,status,notify,alerted,created_at) "
                "VALUES(?,?,?,?,?,'planned',?,0,?)",
                (user_id, day, title, channel_id, note, 1 if notify else 0, time.time()))
    row = store._one("SELECT * FROM content_plan WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    return row


def items(store, start=None, end=None, user_id=1):
    _ensure(store)
    q, p = "SELECT * FROM content_plan WHERE user_id=?", [user_id]
    if start:
        q += " AND day>=?"; p.append(start)
    if end:
        q += " AND day<=?"; p.append(end)
    return store._all(q + " ORDER BY day, id", tuple(p))


def update(store, pid, fields, user_id=1):
    _ensure(store)
    allowed = {"day", "title", "channel_id", "note", "status", "notify"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(1 if (k == "notify" and v) else (0 if k == "notify" else v))
    if not sets:
        return None
    vals += [pid, user_id]
    store._exec(f"UPDATE content_plan SET {','.join(sets)} WHERE id=? AND user_id=?", tuple(vals))
    return store._one("SELECT * FROM content_plan WHERE id=? AND user_id=?", (pid, user_id))


def delete(store, pid, user_id=1):
    _ensure(store)
    store._exec("DELETE FROM content_plan WHERE id=? AND user_id=?", (pid, user_id))


def check_due(store, cfg=None, user_id=1):
    """Emit one reminder alert per due, notify-on, not-yet-alerted plan item. Alerts are
    delivered to Discord by the normal alerts pipeline. Returns how many were raised."""
    _ensure(store)
    today = date.today().isoformat()
    due = store._all("SELECT * FROM content_plan WHERE user_id=? AND notify=1 AND alerted=0 "
                     "AND status!='done' AND day<=?", (user_id, today))
    n = 0
    for it in due:
        ok = store.add_alert({
            "key": f"plan:{it['id']}", "channel_id": it.get("channel_id"), "kind": "plan_due",
            "severity": "info", "title": f"Post due: {it.get('title') or 'planned video'}",
            "message": f"Your content plan has \"{it.get('title')}\" scheduled for {it['day']}."
                       + (f" — {it['note']}" if it.get("note") else ""),
            "link": ""}, user_id=user_id)
        store._exec("UPDATE content_plan SET alerted=1 WHERE id=?", (it["id"],))
        n += int(bool(ok))
    return n
