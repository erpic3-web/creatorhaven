# CreatorHaven extension — loading & manual QA

Folder: `D:\Claude\studioforge\extension` (this folder IS the unpacked extension — no build step).

## Load in Chrome / Edge / Brave (Manifest V3)

1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode** (top-right toggle).
3. Click **Load unpacked** and pick `D:\Claude\studioforge\extension`.
4. Pin the orange **SF** icon from the puzzle-piece menu.
5. Chrome may list two harmless warnings under *Details → Errors*: `browser_specific_settings`
   (a Firefox-only key) and `background.scripts` (the Firefox background entry; Chrome ≥ 121
   ignores it and uses `service_worker`). Neither stops the extension.
6. After editing any file: `chrome://extensions` → the extension's **↻ reload** button, then
   reload the YouTube / Studio tab (content scripts only inject on page load).
7. Background logs: *Details → Inspect views: service worker*. Content-script logs: the page's
   DevTools console, filter `[SF]`.

## Load in Firefox (temporary add-on)

Needs Firefox **128+** (MAIN-world content scripts). Loaded this way the add-on disappears
when Firefox restarts; that is normal for `about:debugging` installs.

1. Open `about:debugging#/runtime/this-firefox`.
2. **Load Temporary Add-on…** → pick `D:\Claude\studioforge\extension\manifest.json`.
3. **Host permissions are opt-in in Firefox MV3.** Open `about:addons` → CreatorHaven →
   *Permissions* tab and enable access for `www.youtube.com`, `studio.youtube.com`,
   `127.0.0.1` and `i.ytimg.com`, or the content scripts will not run. (Alternatively click the
   extension icon in the toolbar and choose "Always allow on youtube.com".)
4. Background logs: the **Inspect** button next to the add-on on `about:debugging`.

## Connect the site

1. Start the companion site (Flask on `http://127.0.0.1:5800`).
2. Click the SF icon → *Site connection* → paste the extension token → **Test connection**.
   You should see `Connected: <app> v<version>` and a green **•** badge on the icon.
3. A site on any other host: type its URL, press *Test connection* — the browser asks for
   the host permission (declared as optional), grant it, the test then runs.

## Tests (no browser needed)

    cd D:\Claude\studioforge\extension
    node test/run.js          # unit tests for lib/common.js + static checks, prints ALL PASS n/n
    node tools/make_icons.js  # regenerates icons/icon{16,48,128}.png

---

## Manual QA checklist (per feature)

Reload the extension, then open a fresh tab for each block. Watch the page console for `[SF]`
warnings — a feature that cannot find its target logs once and stays quiet.

### Popup
- [ ] Opens as a dark 380 px panel; every toggle reflects storage; flipping one shows **Saved**.
- [ ] *Test connection* with the site down → red `Failed: …`; with the site up → green `Connected: … v…` and the badge dot appears.
- [ ] *Open site* opens `siteUrl` in a new tab; *Thumbnail Tester* opens `tester.html`.
- [ ] Appearance / size sub-controls grey out while their master toggle is off.
- [ ] Probe section shows counts after visiting Studio; *Export probe log* copies JSON (paste into a text editor); *Clear* resets to 0.

### youtube.com
1. **confirmSignOut** — avatar menu → *Sign out* → a confirm dialog appears; *Cancel* keeps you signed in.
2. **exactDates** — open any `/watch` URL: the "3 weeks ago" text gets ` · Sep 8, 2026, 3:41 PM`. Navigate to another video via a suggestion (SPA navigation): the date either updates or is absent (never wrong) — see "Known limits".
3. **videoTags** — on a video that has tags (most large channels): a *Tags (n):* chip row under the description box; clicking a chip copies it (toast "Copied: …"). A video without tags shows nothing.
4. **thumbDownloader** — a *⬇ Thumbnail* pill next to Like/Share; click opens the best-resolution image in a new tab; *⧉* copies the URL. Try an old low-res upload (falls back from maxres).
5. **playlistSearch** — open a playlist page: a search box above the list filters rows live; scroll to lazy-load more rows → the filter still applies.
6. **feedCleaner** — home page: Shorts shelf, Playables, "Latest YouTube posts", "Breaking news" shelves and the Shorts sidebar entry are gone; search results have no Shorts shelf. Toggle off → they reappear after a page reload (or immediately, on the next DOM pass).
7. **shortsRedirect** (off by default) — enable, open `youtube.com/shorts/<id>` → lands on `/watch?v=<id>`.
8. **realNames / subCounts** — with the site connected and a token: comment handles (`@someone`) become display names with the handle in the tooltip and a `· 1.2M` badge. Without a token: nothing changes, one `[SF] realNames: no site token` info line in the console.
9. **dontRecommend** (off by default) — enable, hover a home-feed card: a black ✕ in the card's top-left; click → the card's ⋮ menu opens and "Don't recommend channel" is clicked for you; on cards without that option a toast says "Not available here".
10. **favorites** — sidebar *Subscriptions*: hover an entry → ☆ on the right; click → ★ and a *Favorites* block appears above Subscriptions listing it; ✕ in the block removes it; count in the popup updates.
11. **appearance** (off) — enable, pick an accent: Subscribe buttons / progress bar / notification dots take the colour; a font family name changes the page font; *Compact grid* adds one card per row on the home grid.
12. **sizeCustomizer** (off) — enable, drag *Player width* to 130 %: the watch-page player grows; *Comment font* changes comment text size.

### studio.youtube.com
13. **confirmSignOut** — sign-out links rendered in Studio's own DOM get the confirm (Studio's avatar menu is a Google iframe and cannot be intercepted — expected).
14. **studioDiscordButton** — set a Discord URL in the popup: a blue *Discord* pill appears at the left of the header's right section (fallback: fixed top-right) and opens the URL in a new tab.
15. **streamerMode** — toggle in the popup or press **Alt+Shift+H** on Studio: every number with 2+ digits (views, revenue, subscriber counts, dates) blurs, including inside Studio's shadow-DOM tables; new rows that load while scrolling blur too; toggling off restores clean text with no leftover spans.
16. **studioProbe** — open Studio's Analytics; popup → Probe section shows `n endpoints · m responses · h hits`; *Export* → JSON with entries `{url, status, keys, size, count, firstSeen, lastSeen, hits[]}`; entries for realtime endpoints carry hits with paths like `$.…realtime…`. With a token + site up, the site receives `POST /api/ext/realtime` at most every 5 s. Turn the toggle off → the counter stops growing. Studio itself keeps working normally (uploads, analytics, comments).

### Thumbnail Tester
- [ ] Drop / choose 1–4 images → A/B/C/D labels; sections: home grid, sidebar, search, mobile, "among competitors" (each in light + dark, or one theme via the selector).
- [ ] Title / channel / views / age / duration inputs re-render live; *Shuffle competitors* reorders; *Clear* empties; ✕ removes one image.

## Known limits (by design, documented in BUILD_REPORT.md)
- exactDates / videoTags read YouTube's `<head>` microdata, which is written for the first video loaded; after SPA navigation the extension verifies the metadata belongs to the current video and otherwise shows nothing rather than a wrong value. Feeds/search dates are not attempted in v1.
- appearance / sizeCustomizer are best-effort CSS-variable overrides; YouTube renames tokens between builds.
- Studio's account menu (sign-out) lives in a cross-origin iframe.
- Firefox: host permissions must be granted by hand (MV3 rule); MAIN-world scripts need Firefox 128+.
