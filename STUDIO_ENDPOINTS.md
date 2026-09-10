# YouTube Studio internal API — what was mapped on 2026-09-09

Captured by wrapping fetch/XMLHttpRequest inside a logged-in Studio tab and
watching Studio's own calls while navigating Dashboard → Analytics → Content.
No credentials were replayed; the extension will make these calls itself in the
user's browser, the way Studio does. Session tokens seen in request bodies are
deliberately not recorded here.

## Client identity (from `ytcfg.data_` on any Studio page)

| key | value seen |
|---|---|
| INNERTUBE_CLIENT_NAME | `WEB_CREATOR` (numeric `clientName: 62` inside request context) |
| INNERTUBE_CLIENT_VERSION | `1.20260908.05.00` (changes with every Studio release; read it live) |
| INNERTUBE_API_KEY | `AIzaSyBUPetSUmoZL-OhlxA7wSac5XinrygCqMo` (Studio's public web key, sent as `?key=`) |
| INNERTUBE_CONTEXT | the full `context` object Studio posts (client, request, user) — copy it wholesale |
| DELEGATED_SESSION_ID | brand-account id; requests carry it as `user.onBehalfOfUser` and header `X-Goog-PageId` |
| SESSION_INDEX | account slot; header `X-Goog-AuthUser` |
| CHANNEL_ID | the channel currently open in Studio |
| XSRF_TOKEN | present; not needed for youtubei JSON calls |

Every internal call is `POST https://studio.youtube.com/youtubei/v1/<method>?alt=json&key=<INNERTUBE_API_KEY>`
with `Content-Type: application/json`, cookies, `X-Origin: https://studio.youtube.com`,
`X-Goog-AuthUser`, `X-Goog-PageId`, and `Authorization: SAPISIDHASH <unix>_<sha1(unix + " " + SAPISID + " " + origin)>`
(the standard Google web-client scheme; SAPISID is a JS-readable cookie). Studio itself
uses XMLHttpRequest for these, so any interceptor must wrap XHR, not only fetch.

Request body = `{ ...method-specific fields, context: <INNERTUBE_CONTEXT>, user: { onBehalfOfUser } }`.
The context carries `request.sessionInfo.token`, `eats` and `consistencyTokenJars`; reuse
Studio's own context object rather than building one.

## Endpoints observed

| page | method | notes |
|---|---|---|
| Analytics | `yta_web/join` | **the analytics query engine** (see below); 10 KB on a small channel |
| Analytics | `yta_web/get_screen` | screen layout + card configs (`cards`, `sideEntities`, `layout`), ~76 KB |
| Analytics | `yta_web/get_cards` | card data (seen in the summary, body not captured) |
| Analytics, Content | `creator/get_creator_channels` | channels for the login with stats, ~19–21 KB |
| Analytics, Content | `creator/list_creator_playlists` | playlists (`playlistsTotalSize`) |
| all | `creator/get_creator_communications` | `{channelId, communicationType: "COMMUNICATION_TYPE_YTA"}` → notices |
| Content | `creator/check_creator_bulk_action`, `creator/check_creator_bulk_delete` | pending bulk jobs |
| Content | `promotions/get_account` | `accountData`, `adstubeConfigs` |
| Content | `get_survey` | survey prompt |

Not captured this time (the SPA served them from memory before the interceptor was in
place): the content-page video list (expected `creator/list_creator_videos`), the
dashboard's realtime card, and per-video analytics. The extension's Studio probe logs
every one of them the first time the user opens those pages.

## `yta_web/join` — the analytics query language

Request:

```json
{ "nodes": [
  { "key": "0__DASHBOARD_FACT_ANALYTICS_CURRENT",
    "value": { "query": {
      "dimensions": [],
      "metrics": [ { "type": "EXTERNAL_VIEWS" }, { "type": "EXTERNAL_WATCH_TIME" }, { "type": "SUBSCRIBERS_NET_CHANGE" } ],
      "restricts": [ { "dimension": { "type": "USER" }, "inValues": [ "UC…channel id…" ] } ],
      "orders": [],
      "timeRange": { "dateIdRange": { "inclusiveStart": 20260812, "exclusiveEnd": 20260909 } },
      "currency": "USD", "returnDataInNewFormat": true, "limitedToBatchedData": false } } },
  { "key": "0__TOP_VIDEOS", "value": { "query": { "dimensions": [ { "type": "VIDEO" } ], "metrics": [ { "type": "EXTERNAL_VIEWS" } ], "restricts": [ … ], "timeRange": { … } } } },
  { "key": "0__DASHBOARD_FACT_ANALYTICS_LIFETIME_SUBSCRIBERS", "value": { "query": { "timeRange": { "unboundedRange": {} }, … } } },
  { "key": "0__DASHBOARD_FACT_ANALYTICS_TYPICAL", "value": { … } },
  { "key": "0__TOP_VIDEOS_VIDEO", "value": { … } } ],
  "context": …, "user": … }
```

Response: `results[] = { key, value }` where a query returns

```json
{ "resultTable": {
    "dimensionColumns": [ { "dimension": { "type": "VIDEO" }, "strings": { "values": [ "WdyzXLW0yDo" ] } } ],
    "metricColumns": [ { "metric": { "type": "EXTERNAL_VIEWS", "asPercentagesOfTotal": false, "includeTotal": false, "cumulative": false },
                         "counts": { "values": [ 1547 ] }, "columnAnomalies": { "ids": [ 0 ] } } ],
    "anomalyContext": [ … ] } }
```

and the TYPICAL node returns `getTypicalPerformance.result.metricColumns[].currentValue`.
Date ids are `YYYYMMDD` integers; `exclusiveEnd` is tomorrow's date for "up to today".
`EXTERNAL_VIEWS` is the post-2026-08-24 play-start "views" metric.

## Still to confirm through the probe (user browses Studio with the extension loaded)

1. The engaged-views metric type (Studio's Advanced mode "Engaged views" column; expected
   `EXTERNAL_ENGAGED_VIEWS` or the legacy `VIEWS`).
2. The realtime query (last 48 h / 60 min): metric + time range shape on the dashboard card.
3. `creator/list_creator_videos` request shape (filters, sort, page size, which columns the
   response carries: privacy, publishAt, monetization, restrictions, madeForKids).
4. Ad-break editing (`creator/update_video`? with `monetizationSettings`/`adBreaks`), comment
   heart (`comment/perform_comment_action`?), collaborations. Write actions come last and only
   behind a confirmation in the extension.


## DELEGATED CHANNELS = THE EXTENSION PATH (decided 2026-09-09)

The Bloxi network's channels are **YouTube Studio delegated** to bloxistudios2 (invited by
email as Viewer/Manager under Studio > Settings > Permissions), NOT brand-owned by it.
Consequences, confirmed with the user:
- **OAuth / the Analytics API cannot reach Studio-delegated channels.** OAuth's channel
  picker + `channel==MINE` only list channels the Google account OWNS or brand-manages.
  Studio Permissions (the email-invite system) is invisible to OAuth by design. So the
  official-API path (sf/api_analytics.py, ids=channel==MINE) only covers the handful of
  channels bloxistudios2 actually owns (the 4 already imported linked before = owned).
- **No YouTube Content Manager / CMS** (would have allowed onBehalfOfContentOwner = one
  login for the whole network; the user does not have one). Site has no content-owner code.
- **Therefore the delegated majority goes through the EXTENSION**, reading bloxistudios2's
  own logged-in Studio session (the NewStudio mechanism): clone Studio's `yta_web/join`
  analytics call per channel (enumerate via `creator/get_creator_channels`, switch channel
  via onBehalfOfUser / X-Goog-PageId), post to the site. Viewer-level perms hide revenue;
  totals + engaged views are available.

**BUILD STATE / TODO (as of 2026-09-09):** extension is built (node test/run.js 51/51) but
never loaded in a browser; its Studio probe only OBSERVES + posts realtime samples, and the
site's ext API (/api/ext/ping, /api/ext/channels, /api/ext/realtime) has NO per-channel
analytics ingest yet. NEXT: user loads the unpacked extension in the Chrome where
bloxistudios2 is signed into Studio, runs the probe (switch through delegated channels +
Dashboard/Analytics/Content), exports the probe log. From that capture, build: (a) extension
active fetcher = enumerate delegated channels + clone yta_web/join per channel; (b) site
ingest endpoint (e.g. /api/ext/analytics) + store table for extension-sourced channels;
(c) dashboard/channels/calendar rendering of delegated channels alongside API-linked ones.
Site OAuth client is now the gen-lang "Studio Haven desktop" (id tail 8288...com); the 4
owned channels still use the API.
