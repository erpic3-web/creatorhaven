"""Configuration for CreatorHaven.

Order of precedence: environment variables > data/config.json > fallbacks that
reuse credentials already on this machine (the Gemini key in dti-thumbs and the
Google OAuth client the yt-schedule-bot already links channels with).

Nothing here ever prints a secret; `public_view()` masks them for the UI.
"""
import json
import os
import secrets
from pathlib import Path

APP_NAME = "CreatorHaven"
APP_VERSION = "0.2.0"

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG_PATH = DATA / "config.json"

# Existing local sources of credentials (all the user's own, on this PC).
DTI_CONFIG = Path(r"D:\Claude\dti-thumbs\data\config.json")
SCHEDULE_BOT = Path(r"D:\Claude\yt-schedule-bot")

DEFAULTS = {
    "site_password": "",              # empty = no login gate (local use)
    "secret_key": "",                 # generated once and persisted
    "port": 5800,
    "base_url": "",                   # "" -> http://127.0.0.1:<port>
    "database": "",                   # "" -> data/studioforge.sqlite3
    "database_url": "",               # hosted: Postgres URL that keeps the SQLite file durable (sf/persist.py)
    "persist_minutes": 10,            # hosted: how often the SQLite file is pushed to Postgres
    "gemini_api_key": "",
    "youtube_api_key": "",            # optional; OAuth tokens work for public reads too
    "google": {
        "client_id": "",
        "client_secret": "",
        "client_type": "desktop",     # desktop (any loopback port) | web (registered redirect)
    },
    "discord_webhook_url": "",
    # --- multi-user sign-in ---------------------------------------------------
    "require_login": True,            # gate the whole app behind an account (accounts, not a password)
    "owner_email": "",               # if set, the account with this email claims the operator's channels
    "owner_password": "",            # hosted: account 1 (OWNER_EMAIL) is created at boot with this password; never shown
    "contact_email": "",             # shown on /privacy as the contact address (empty = the store listing's address)
    "admin_emails": [],              # owner-managed whitelist: these accounts get the admin (special) settings + AI
    "ai_whitelist_only": True,       # gate the paid AI features (Gemini) behind owner + whitelist
    "thumbs_daily_free": 5,          # thumbnails any other signed-in account may generate per day (0 = whitelist only)
    "ext_daily_units": 400,          # YouTube quota units a non-owner account's extension may spend per day
    "stats_retention_days": 30,      # other channels' daily counters are kept this long (YouTube API policy III.E.4.d;
                                     # 1095 once YouTube grants the III.L "derived metrics and data storage" allowance)
    "google_signin": {               # "log in with Google" (web OAuth app on this host)
        "client_id": "",
        "client_secret": "",
    },
    "discord_oauth": {               # "log in with Discord"
        "client_id": "",
        "client_secret": "",
    },
    "scan_per_channel": 200,          # newest uploads scanned per sync
    "analytics_days": 90,
    "sync_minutes": 30,               # background refresh cadence (0 = manual only)
    "ext_token": "",                  # shared secret for the browser extension
}

_ENV = {
    "SITE_PASSWORD": ("site_password",),
    "SECRET_KEY": ("secret_key",),
    "PORT": ("port",),
    "BASE_URL": ("base_url",),
    "DATABASE_PATH": ("database",),
    "DATABASE_URL": ("database_url",),
    "PERSIST_MINUTES": ("persist_minutes",),
    "GEMINI_API_KEY": ("gemini_api_key",),
    "YOUTUBE_API_KEY": ("youtube_api_key",),
    "GOOGLE_CLIENT_ID": ("google", "client_id"),
    "GOOGLE_CLIENT_SECRET": ("google", "client_secret"),
    "GOOGLE_CLIENT_TYPE": ("google", "client_type"),
    "DISCORD_WEBHOOK_URL": ("discord_webhook_url",),
    "REQUIRE_LOGIN": ("require_login",),
    "OWNER_EMAIL": ("owner_email",),
    "OWNER_PASSWORD": ("owner_password",),
    "CONTACT_EMAIL": ("contact_email",),
    "GOOGLE_SIGNIN_CLIENT_ID": ("google_signin", "client_id"),
    "GOOGLE_SIGNIN_CLIENT_SECRET": ("google_signin", "client_secret"),
    "DISCORD_CLIENT_ID": ("discord_oauth", "client_id"),
    "DISCORD_CLIENT_SECRET": ("discord_oauth", "client_secret"),
    "EXT_TOKEN": ("ext_token",),
    "STATS_RETENTION_DAYS": ("stats_retention_days",),
}


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _set(d, keys, value):
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _get(d, keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def load(path=None):
    """Return the merged config dict. Creates data/ and persists generated secrets."""
    path = Path(path) if path else CONFIG_PATH
    cfg = json.loads(json.dumps(DEFAULTS))
    file_cfg = _read_json(path) if path.exists() else {}
    for k, v in file_cfg.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    for env, keys in _ENV.items():
        val = os.environ.get(env)
        if val is None or val == "":
            continue
        if keys in (("require_login",),):
            _set(cfg, keys, val.strip().lower() not in ("0", "false", "no", "off"))
        elif keys == ("port",) and val.isdigit():
            _set(cfg, keys, int(val))
        else:
            _set(cfg, keys, val)

    changed = False
    if not cfg["secret_key"]:
        cfg["secret_key"] = secrets.token_hex(32)
        changed = True
    if not cfg["ext_token"]:
        cfg["ext_token"] = secrets.token_urlsafe(24)
        changed = True
    if not cfg["gemini_api_key"]:
        cfg["gemini_api_key"] = _read_json(DTI_CONFIG).get("gemini_api_key", "")
    if not cfg["google"]["client_id"]:
        sb = _read_json(SCHEDULE_BOT / "config.json").get("google", {})
        if sb.get("clientId"):
            cfg["google"]["client_id"] = sb["clientId"]
            cfg["google"]["client_secret"] = sb.get("clientSecret", "")
            cfg["google"]["client_type"] = "desktop"
    if not cfg["base_url"]:
        # Render exposes the public URL of the service; locally fall back to the loopback port
        cfg["base_url"] = os.environ.get("RENDER_EXTERNAL_URL") or f"http://127.0.0.1:{cfg['port']}"
    try:
        cfg["port"] = int(cfg["port"])
    except (TypeError, ValueError):
        cfg["port"] = 5800
    if not cfg["database"]:
        cfg["database"] = str(DATA / "studioforge.sqlite3")

    if changed and not os.environ.get("SF_NO_PERSIST"):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            persist = {k: v for k, v in file_cfg.items()}
            persist["secret_key"] = cfg["secret_key"]
            persist["ext_token"] = cfg["ext_token"]
            path.write_text(json.dumps(persist, indent=2), encoding="utf-8")
        except Exception:
            pass
    return cfg


def _mask(v):
    if not v:
        return ""
    v = str(v)
    return v[:4] + "…" + v[-3:] if len(v) > 10 else "•" * len(v)


def public_view(cfg):
    """What the UI may see: presence and masked tails, never the values."""
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "port": cfg["port"],
        "base_url": cfg["base_url"],
        "login_required": bool(cfg.get("require_login")),
        "auth": {
            "password": True,
            "google": bool(cfg["google_signin"]["client_id"] and cfg["google_signin"]["client_secret"]),
            "discord": bool(cfg["discord_oauth"]["client_id"] and cfg["discord_oauth"]["client_secret"]),
        },
        "gemini": bool(cfg["gemini_api_key"]),
        "youtube_api_key": bool(cfg["youtube_api_key"]),
        "google_client": bool(cfg["google"]["client_id"] and cfg["google"]["client_secret"]),
        "google_client_id_tail": _mask(cfg["google"]["client_id"]),
        "google_client_type": cfg["google"]["client_type"],
        "discord": bool(cfg["discord_webhook_url"]),
        "ext_token": cfg["ext_token"],
        "sync_minutes": cfg["sync_minutes"],
        "scan_per_channel": cfg["scan_per_channel"],
        "analytics_days": cfg["analytics_days"],
        "schedule_bot_present": (SCHEDULE_BOT / "data" / "tokens").exists(),
    }
