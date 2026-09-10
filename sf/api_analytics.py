"""YouTube Analytics API v2 (reports.query) over urllib.

`engagedViews` is a core metric next to `views` for any channel you are
authorized on, which is what makes the exact-numbers / engaged-views half of
the product official rather than scraped. Data lags one to three days; today
is never in here (realtime comes from the extension's Studio capture).
"""
from datetime import date, timedelta

from . import http

ANALYTICS_BASE = "https://youtubeanalytics.googleapis.com/v2"

CORE = ["views", "engagedViews", "estimatedMinutesWatched", "averageViewDuration",
        "subscribersGained", "subscribersLost", "likes", "comments", "shares"]
MONETARY = ["estimatedRevenue"]


class NeedsScope(Exception):
    """403 insufficient scope: the channel must be re-linked with the analytics scope."""


class ApiNotEnabled(Exception):
    """403 accessNotConfigured: enable the YouTube Analytics API in the Cloud project."""


def _classify(err):
    reason = (err.reason or "").lower()
    msg = (err.message or "").lower()
    if err.status == 403:
        if "accessnotconfigured" in reason or "has not been used in project" in msg or "is disabled" in msg:
            raise ApiNotEnabled(err.message)
        if "insufficient" in reason or "insufficient" in msg or "scope" in msg or "permission" in reason:
            raise NeedsScope(err.message)
    raise err


class Analytics:
    def __init__(self, access_token, base=ANALYTICS_BASE, log_cb=None):
        self.token = access_token
        self.base = base
        self.log_cb = log_cb

    def query(self, start, end, metrics, dimensions=None, sort=None, filters=None, max_results=None,
              ids="channel==MINE"):
        params = {"ids": ids, "startDate": start, "endDate": end, "metrics": ",".join(metrics),
                  "dimensions": ",".join(dimensions) if dimensions else None, "sort": sort,
                  "filters": filters, "maxResults": max_results}
        url = f"{self.base}/reports?{http.qs(params)}"
        if self.log_cb:
            self.log_cb(url)
        try:
            j = http.get_json(url, headers={"Authorization": f"Bearer {self.token}"})
        except http.HttpError as e:
            _classify(e)
        cols = [c["name"] for c in j.get("columnHeaders", [])]
        return {"columns": cols, "rows": [dict(zip(cols, r)) for r in j.get("rows", [])]}

    def _query_with_fallback(self, start, end, metrics, **kw):
        """Retry without engagedViews / monetary metrics when the API rejects them."""
        try:
            return self.query(start, end, metrics, **kw)
        except http.HttpError as e:
            if e.status != 400:
                raise
            msg = (e.message or "").lower()
            trimmed = [m for m in metrics if not (
                (m == "engagedViews" and "engagedviews" in msg) or
                (m in MONETARY and ("revenue" in msg or "monetary" in msg)))]
            if trimmed == metrics:
                raise
            return self.query(start, end, trimmed, **kw)

    def daily(self, days=90, monetary=False, end=None):
        end = end or (date.today() - timedelta(days=1))
        start = end - timedelta(days=days - 1)
        metrics = CORE + (MONETARY if monetary else [])
        res = self._query_with_fallback(start.isoformat(), end.isoformat(), metrics,
                                        dimensions=["day"], sort="day")
        out = []
        for r in res["rows"]:
            out.append({"day": r.get("day"), "views": r.get("views"), "engaged_views": r.get("engagedViews"),
                        "minutes": r.get("estimatedMinutesWatched"), "avg_view_s": r.get("averageViewDuration"),
                        "subs_gained": r.get("subscribersGained"), "subs_lost": r.get("subscribersLost"),
                        "likes": r.get("likes"), "comments": r.get("comments"), "shares": r.get("shares"),
                        "revenue": r.get("estimatedRevenue")})
        return out

    def top_videos(self, days=28, n=200, end=None):
        end = end or (date.today() - timedelta(days=1))
        start = end - timedelta(days=days - 1)
        metrics = ["views", "engagedViews", "estimatedMinutesWatched"]
        try:
            res = self._query_with_fallback(start.isoformat(), end.isoformat(), metrics,
                                            dimensions=["video"], sort="-views", max_results=n)
        except http.HttpError as e:
            if e.status == 400 and n > 50:
                res = self._query_with_fallback(start.isoformat(), end.isoformat(), metrics,
                                                dimensions=["video"], sort="-views", max_results=50)
            else:
                raise
        return [{"video_id": r.get("video"), "views": r.get("views"), "engaged_views": r.get("engagedViews"),
                 "minutes": r.get("estimatedMinutesWatched")} for r in res["rows"]]

    def totals(self, days=28, monetary=False, end=None):
        end = end or (date.today() - timedelta(days=1))
        start = end - timedelta(days=days - 1)
        metrics = CORE + (MONETARY if monetary else [])
        res = self._query_with_fallback(start.isoformat(), end.isoformat(), metrics)
        return res["rows"][0] if res["rows"] else {}
