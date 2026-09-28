"""Authentication for CreatorHaven — email/password plus Google and Discord sign-in.

Passwords are PBKDF2-HMAC-SHA256 (stdlib hashlib), 200k iterations, per-user salt; the
plaintext never touches disk. OAuth sign-in ("log in WITH Google/Discord") is a lightweight
identity flow — separate from the YouTube channel-linking OAuth in sf/oauth.py, which asks for
Analytics scopes. Here we only want who the user is (email, name, avatar).

Each provider is optional: the buttons appear only when the operator has configured that
provider's client id/secret (its own OAuth app + redirect URI on this host).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse

from . import http

_ITER = 200_000


# ------------------------------------------------------------------ passwords
def hash_password(password):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
    return salt.hex(), dk.hex()


def verify_password(password, salt_hex, hash_hex):
    if not salt_hex or not hash_hex:
        return False
    try:
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _ITER)
    except Exception:
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def valid_email(e):
    e = (e or "").strip()
    return "@" in e and "." in e.split("@")[-1] and len(e) <= 200 and " " not in e


# --------------------------------------------------------------------- google
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


def google_configured(cfg):
    g = cfg.get("google_signin") or {}
    return bool(g.get("client_id") and g.get("client_secret"))


def google_auth_url(cfg, redirect_uri, state):
    g = cfg["google_signin"]
    q = {"client_id": g["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
         "scope": "openid email profile", "state": state, "access_type": "online",
         "prompt": "select_account"}
    return GOOGLE_AUTH + "?" + urllib.parse.urlencode(q)


def google_exchange(cfg, code, redirect_uri, token_base=None):
    g = cfg["google_signin"]
    body, _ = http.request(token_base or GOOGLE_TOKEN, method="POST", data={
        "code": code, "client_id": g["client_id"], "client_secret": g["client_secret"],
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"}, timeout=20)
    tok = json.loads(body.decode())
    claims = _decode_id_token(tok.get("id_token", ""))
    return {"provider": "google", "sub": claims.get("sub"), "email": claims.get("email"),
            "email_verified": bool(claims.get("email_verified")),
            "name": claims.get("name") or (claims.get("email") or "").split("@")[0],
            "avatar": claims.get("picture")}


def _decode_id_token(jwt):
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


# -------------------------------------------------------------------- discord
DISCORD_AUTH = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN = "https://discord.com/api/oauth2/token"
DISCORD_ME = "https://discord.com/api/users/@me"


def discord_configured(cfg):
    d = cfg.get("discord_oauth") or {}
    return bool(d.get("client_id") and d.get("client_secret"))


def discord_auth_url(cfg, redirect_uri, state):
    d = cfg["discord_oauth"]
    q = {"client_id": d["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
         "scope": "identify email", "state": state, "prompt": "consent"}
    return DISCORD_AUTH + "?" + urllib.parse.urlencode(q)


def discord_exchange(cfg, code, redirect_uri, token_base=None, me_base=None):
    d = cfg["discord_oauth"]
    body, _ = http.request(token_base or DISCORD_TOKEN, method="POST", data={
        "code": code, "client_id": d["client_id"], "client_secret": d["client_secret"],
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"}, timeout=20)
    tok = json.loads(body.decode())
    at = tok.get("access_token")
    ub, _ = http.request(me_base or DISCORD_ME, headers={"Authorization": "Bearer " + at}, timeout=20)
    u = json.loads(ub.decode())
    avatar = (f"https://cdn.discordapp.com/avatars/{u['id']}/{u['avatar']}.png"
              if u.get("avatar") else None)
    return {"provider": "discord", "sub": str(u.get("id")), "email": u.get("email"),
            "email_verified": bool(u.get("verified")),
            "name": u.get("global_name") or u.get("username") or "creator", "avatar": avatar}


def new_state():
    return secrets.token_urlsafe(24)
