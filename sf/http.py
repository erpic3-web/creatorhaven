"""One small HTTP helper (urllib, no requests) used by every API client.

Retries only what is worth retrying: 429 and 5xx. A 4xx means WE sent something
wrong and repeating it just multiplies the same error (mc-analog-editor lesson).
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 60
USER_AGENT = "CreatorHaven/0.1 (+local)"


class HttpError(Exception):
    def __init__(self, status, body, url=""):
        self.status = status
        self.body = body or ""
        self.url = url
        short = " ".join(self.body.split())[:300]
        super().__init__(f"HTTP {status} {url.split('?')[0]}: {short}")

    @property
    def retryable(self):
        return self.status == 429 or self.status >= 500

    def json(self):
        try:
            return json.loads(self.body)
        except Exception:
            return {}

    @property
    def reason(self):
        """Google error reason, e.g. insufficientPermissions, accessNotConfigured, quotaExceeded."""
        j = self.json()
        try:
            errs = j["error"]["errors"]
            if errs:
                return errs[0].get("reason", "")
        except Exception:
            pass
        try:
            for d in j["error"].get("details", []):
                if d.get("reason"):
                    return d["reason"]
        except Exception:
            pass
        return ""

    @property
    def message(self):
        j = self.json()
        try:
            return j["error"].get("message", "") or j.get("error_description", "") or self.body[:200]
        except Exception:
            return self.body[:200]


def request(url, *, method=None, data=None, headers=None, timeout=DEFAULT_TIMEOUT, retries=2):
    """Return (bytes, headers). Raises HttpError. `data` may be bytes, dict (form) or str."""
    if isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
        headers = dict(headers or {})
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif isinstance(data, str):
        data = data.encode()
    hdrs = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    attempt = 0
    while True:
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            err = HttpError(e.code, body, url)
            if err.retryable and attempt < retries:
                attempt += 1
                time.sleep(1.5 * attempt)
                continue
            raise err
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                attempt += 1
                time.sleep(1.5 * attempt)
                continue
            raise HttpError(0, str(e), url)


def get_json(url, headers=None, timeout=DEFAULT_TIMEOUT, retries=2):
    body, _ = request(url, headers=headers, timeout=timeout, retries=retries)
    return json.loads(body.decode("utf-8")) if body else {}


def post_json(url, payload, headers=None, timeout=DEFAULT_TIMEOUT, retries=1, method="POST"):
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    body, _ = request(url, method=method, data=json.dumps(payload).encode(), headers=hdrs,
                      timeout=timeout, retries=retries)
    return json.loads(body.decode("utf-8")) if body else {}


def post_form(url, form, headers=None, timeout=DEFAULT_TIMEOUT):
    body, _ = request(url, method="POST", data=form, headers=headers, timeout=timeout, retries=1)
    return json.loads(body.decode("utf-8")) if body else {}


def qs(params):
    """URL-encode a params dict, dropping None values."""
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
