"""Google OAuth 2.0 (authorization code, offline access) for linking channels.

A DESKTOP client type may redirect to any loopback port, which is why the
yt-schedule-bot's existing client works for the local site without registering
a redirect URI. A hosted deployment needs a WEB client with
<base_url>/oauth/cb registered (README).
"""
import secrets
import urllib.parse

from . import http

AUTH_BASE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_BASE = "https://oauth2.googleapis.com"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

SCOPE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"
SCOPE_FORCE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"   # comments + video edits
SCOPE_ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"
SCOPE_MONETARY = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"

SCOPE_SETS = {
    "readonly": [SCOPE_READONLY],
    "edit": [SCOPE_FORCE_SSL],
    "analytics": [SCOPE_ANALYTICS],
    "monetary": [SCOPE_MONETARY],
}
DEFAULT_SETS = ["readonly", "edit", "analytics", "monetary"]


def scopes_for(sets):
    out = []
    for s in sets or DEFAULT_SETS:
        for sc in SCOPE_SETS.get(s, []):
            if sc not in out:
                out.append(sc)
    return out


def has_scope(scopes_str, scope):
    return scope in (scopes_str or "").split()


def auth_url(cfg, redirect_uri, sets=None, state=None, auth_base=AUTH_BASE):
    state = state or secrets.token_urlsafe(16)
    params = {
        "client_id": cfg["google"]["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes_for(sets)),
        "access_type": "offline",
        "prompt": "consent select_account",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{auth_base}?{urllib.parse.urlencode(params)}", state


def exchange_code(cfg, code, redirect_uri, token_base=TOKEN_BASE):
    """Return the token response dict (access_token, refresh_token, scope, expires_in)."""
    return http.post_form(f"{token_base}/token", {
        "code": code,
        "client_id": cfg["google"]["client_id"],
        "client_secret": cfg["google"]["client_secret"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })


def refresh_access_token(cfg, refresh_token, token_base=TOKEN_BASE):
    """Return (access_token, expires_in_seconds, granted_scope_string)."""
    j = http.post_form(f"{token_base}/token", {
        "client_id": cfg["google"]["client_id"],
        "client_secret": cfg["google"]["client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    return j["access_token"], int(j.get("expires_in", 3600)), j.get("scope", "")


def revoke(token, revoke_url=REVOKE_URL):
    try:
        http.request(revoke_url, method="POST", data={"token": token}, retries=0)
        return True
    except http.HttpError:
        return False
