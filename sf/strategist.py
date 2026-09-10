"""The Strategy brain — a YouTube strategist that works the OUTLIER METHOD for real.

Before every reply it reads three real, id-carrying sources and nothing else:
  1. the operator's tracked network (from the store: every channel, its numbers, its top and
     newest uploads, and a learned style card for the focus channel),
  2. the OUTLIER RADAR (radar.py): recent videos that beat their own channel's median, both
     inside the network and — via the YouTube Data API — across the niche beyond it,
  3. its own memory: every title it has already pitched (so it never hands back the same
     idea twice) and the recent conversation.

It runs on Gemini (same key as the other tools).  When it cites a video it drops
[thumb:VIDEOID] on its own line so the UI renders the real thumbnail; the ids come from the
data above, never from the model's imagination.  Each reply ends with a hidden
[[pitched: ...]] line the server strips and stores — that is the anti-repeat memory.
"""
import json
import random
import re
import statistics
import time
from collections import Counter

from . import gemini, metrics, radar

CHAT_KEY = "strategy:chat"
PITCHED_KEY = "strategy:pitched"
NOTES_KEY = "strategy:notes"
MAX_CHAT = 40
MAX_PITCHED = 200
_PITCHED_RE = re.compile(r"\[\[\s*pitched\s*:(.*?)\]\]\s*$", re.S | re.I)

# ---------------------------------------------------------------------------- the canon
PLAYBOOK = """=== HOW YOUTUBE DECIDES (2026) ===
- Separate recommenders: Home/Browse and Suggested carry entertainment; Search carries how-to;
  the Shorts feed is its own loop. A new upload is shown to the core audience first; its first
  48 h of CTR and average view duration RELATIVE TO THE CHANNEL'S LAST 10 UPLOADS decide whether
  it widens to strangers. Videos are recommended to VIEWERS, not subscribers.
- The signal ladder a creator can move: (1) CTR on impressions = packaging; (2) what happens in
  the first 15-30 s after the click (a high CTR that collapses at 0:30 is demoted as misleading);
  (3) AVD and the SHAPE of the retention curve (gentle slope healthy, cliff = broken promise);
  (4) satisfaction: surveys, repeat views, "not interested", whether the session continues.
- Benchmarks: CTR 4-8% baseline, 10%+ strong, <2% = packaging misaligned. Longform loses 30-50%
  of viewers by 0:30; a strong open keeps it under ~20%; aim for 50-60% of runtime watched.
  Retention FALLS as reach WIDENS (wider audiences are colder) — compare like with like.
- Titles: top performers cluster around 8 words / 45-55 chars; brackets slightly outperform;
  "I"/"you" beat impersonal phrasing. Thumbnails: one focal subject, one visible emotion,
  3-5 words max, judged at 168x94 px. Custom thumbnails ~35% more CTR than auto.

=== PACKAGING IS THE PRODUCT (the idea is 80%) ===
- Small creators spend 95% of their time filming/editing; top creators spend ~30% on the idea
  and the packaging. Most channels have a packaging problem, not a content problem.
- Packaging is decided at IDEATION: write the title and sketch the thumbnail BEFORE filming.
  If the packaging can't be made exciting, the idea is wrong — pick another.
- Thumbnail carries ONE story beat; title adds the twist; never the same sentence twice.
- Title formulas that consistently work: the specific number; the curiosity gap ("I ... and
  this happened"); the restriction/stakes frame ("$1 vs $1,000,000", "with NO ___");
  the transformation (before -> after); the warning ("don't ___ until"); the versus frame;
  the achievement story ("0 to 1M in 6 months"); the year + action; the accusation/exposé
  ("___ is LYING to you"); the forbidden/secret ("the update they HID"). Front-load the
  interesting word; the first 3 words are what the phone shows.
- THE NINE IDEA MECHANISMS (what an outlier is usually made of — name it, then transplant it):
  1 restriction/challenge (a rule that creates stakes), 2 escalation/ladder (1 -> 100 -> 1M),
  3 versus/face-off, 4 mystery/curiosity gap with proof, 5 transformation/makeover,
  6 tier-list/ranking with a reason, 7 social experiment / trolling with a reaction,
  8 exposé/"the truth about", 9 story/roleplay drama with a cliffhanger.
  A format travels across niches; a TOPIC doesn't. Convert the mechanism, never the topic.

=== THUMBNAIL CRAFT ===
- Faces with a real, extreme emotion out-click everything; if no face, one high-value object
  with rim light / outline to separate it from a simple background.
- Rule of thirds: subject/eyes on a third line; exactly ONE primary read, ONE secondary (the
  twist), text LAST. Warm saturated hues (red/orange/yellow/pink) advance, cool recede;
  complementary contrast on the subject; 60-30-10 color split; check in grayscale — if the
  subject doesn't separate by value, recolor. 10-15% breathing room; bottom-right clear
  (duration stamp). Heavy outline on text so it reads at feed size.
- Gaming/kid-niche convention: HOOK VERB + RESTRICTION/DRAMA + game name in the title,
  ALL-CAPS on 2-4 power words, a crying/shocked avatar or facecam, 3-5 thumbnail words with
  the keyword colored, a circle/arrow callout when there's a "look here".

=== THE FIRST 30 SECONDS ===
- The hook turns the CLICK (a promise) into a WATCH (proof + curiosity). Inside 0-15 s stack:
  cold open mid-action (no greeting, no logo), the premise in one breath phrased as stakes,
  visual PROOF the premise is real in the first 10 s, a flash of the end-state without
  resolving it, then a first payoff by 30-45 s. Cut density 2-4x the rest of the video.
- MrBeast's production doc, the parts that generalize: the first minute must PROVE the
  thumbnail; front-load visuals; minutes 1-3, 3-6, 6-end each have a job; re-hook every few
  minutes with a new stake; "no dull moments"; the ending pays off the biggest promise.
- Never in a hook: "welcome back", rules for more than a sentence, dead footage, apologizing.

=== RETENTION + STRUCTURE ===
- Pattern interrupt every 20-40 s (cut, zoom, SFX, insert, chat screenshot); a retention loop
  (tease the next thing) every 15-30 s; one complete idea per 60-90 s in curation content.
- Chapters = mini-payoffs. End on the biggest payoff, then a 7-12 s bridge pitching the next
  video — no generic outro. Silence over ~1.5 s reads as dead air; bed -12 to -18 dB under voice.

=== THE OUTLIER METHOD (your core job) ===
- An OUTLIER is a video that beat its own channel's median: 2x is interesting, 3x+ is worth
  studying, 10x+ is a priority signal. Size of the channel is irrelevant — a 20k channel doing
  15x taught you something a 2M channel doing 1.2x did not.
- For every outlier answer FIVE questions before you pitch anything: (a) which of the nine
  MECHANISMS is it? (b) what promise does the TITLE make and with what structure? (c) what is
  the ONE story beat the THUMBNAIL shows (subject, emotion, prop, text)? (d) why NOW (update,
  trend, drama, season)? (e) what would it look like inside OUR niche with OUR character —
  same mechanism, new subject, better packaging. That last answer is the idea.
- Cross-niche transfer is the whole game: a Minecraft "100 days" ladder becomes a Gorilla Tag
  "100 days" ladder; a Roblox "I pretended to be a noob" experiment becomes a VR one. Copy the
  pattern, not the topic — and beat the original's packaging.
- Series-ify a winner: bring it back weekly with escalation until it decays. Series identity +
  recurring characters/rules = returning viewers, the metric that separates channels from hits.
- React fast: a decent video inside 12 h of a trend beats a polished one 3 days late.

=== SHORTS ===
- Shorts = discovery, longform = money. Posting both grows subs ~3x faster (YouTube 2026 data).
  20-60 s, one loopable beat, hook TEXT in frame 1, no intro. Bridge Shorts to the specific
  longform (same series on both, a CTA, playlists). Shorts CPM runs ~95% below longform.

=== MONEY (gaming / kid niches) ===
- Gaming ad CPM $1-4 (RPM $2-5); kids content lower. So the money is VOLUME, sponsorships
  (game studios, apps, toys — priced on average views per video, integrations $15-80 CPM),
  and platform diversification. YPP: 1,000 subs + 4,000 public watch hours or 10M Shorts
  views in 90 days (rising Feb 2027 to 8,000 h / 20M). Videos over 8 min get mid-rolls.
- Compilation/curation channels monetize when they ADD value (ranking with a reason, a
  through-line, captions/voice, own footage, a consistent editing signature); silent stitches
  of other people's clips are what gets hit by the reused-content policy.

=== READING ANALYTICS (diagnosis) ===
- Impressions up, CTR down -> pushed to a wider/wrong audience. CTR fine, AVD cliff at 0:30 ->
  the video breaks the promise. AVD fine, views flat -> packaging isn't earning impressions;
  test the thumbnail first. Returning viewers rising -> series working. Don't rewrite titles in
  hour 2; the 48-hour read against the last 10 uploads is the earliest honest signal.

=== GROWTH STAGES ===
- 0-1k: one niche, 1-3 games, a recognizable packaging style, a schedule, outliers weekly.
- 1k-100k: series, recurring formats, collabs inside the niche, trend speed, Shorts as discovery.
- 100k+: systems (calendar, batch production, editor + thumbnail artist, weekly analytics
  read), diversification, a brand bigger than one game. Burnout is the silent channel killer.

=== THE VR / ROBLOX / COMPILATION LANE (this operator's world) ===
- Audience mostly 8-16 on phones and TVs; discovery almost entirely Browse/Suggested; 1-3
  games per channel; 4-7 uploads a week on the fastest growers; game updates spike in the
  first 48 h; roleplay drama, restriction challenges, trolling with a reaction, "copy but
  better" of the niche's outliers, and collabs inside the niche are what works.
- MEASURED by this operator (say "we measured"): copy-but-better overperforms on EVERY
  channel studied (up to x2.83 vs a 1.65M median); a Gorilla Tag story channel keeps a music
  bed under 98-100% of the runtime ~8.5 dB below the voice with a vine boom every 2.5-4 min
  on punchlines and the hook cut 2-13x faster than the rest; horror story channels build
  dread by removing mids, not getting louder, and 84% of their scare beats have no spoken cue;
  clip-story shorts run 23-36 s with every clip under 7 s and the final clip landing the payoff.
- Ops rule: keep more than 7 videos scheduled per channel at all times; batch production;
  weekly rhythm per channel; series naming so Suggested chains them."""


# --------------------------------------------------------------------------- grounding
def _style_card(store, channel_id):
    """Learned read of the focus channel's OWN voice, from its real uploads."""
    ch = store.channel(channel_id) if channel_id else None
    if not ch:
        return ""
    vids = [v for v in store.videos(channel_id) if v.get("privacy") == "public" and v.get("title")]
    if len(vids) < 4:
        return ""
    titles = [v["title"] for v in vids[:80]]
    allcaps = round(100 * sum(1 for t in titles if re.search(r"\b[A-Z]{3,}\b", t)) / len(titles))
    emoji = round(100 * sum(1 for t in titles if re.search(r"[\U0001F000-\U0001FAFF☀-➿]", t)) / len(titles))
    qmark = round(100 * sum(1 for t in titles if "?" in t) / len(titles))
    avg_len = round(statistics.mean(len(t) for t in titles))
    shorts = round(100 * sum(1 for v in vids if int(v.get("is_short") or 0)) / len(vids))
    stop = set("the a an and or of to in for i my me you your it is are on with this that how why what was we he she".split())
    words = [w.lower() for t in titles for w in re.findall(r"[A-Za-z']{4,}", t) if w.lower() not in stop]
    common = ", ".join(w for w, _ in Counter(words).most_common(10)) if words else ""
    durs = [v.get("duration_s") or 0 for v in vids if not int(v.get("is_short") or 0) and v.get("duration_s")]
    med_dur = f"{round(statistics.median(durs) / 60)} min" if durs else "n/a"
    return (
        f"Titles average ~{avg_len} chars; {allcaps}% use ALL-CAPS words, {emoji}% use emoji, {qmark}% ask a "
        f"question; {shorts}% of uploads are Shorts; typical longform ~{med_dur}. Recurring title words: {common}.\n"
        "Match THIS voice — ideas, titles and thumbnails should sound like this channel, not a template."
    )


def _vid_line(v, out, indent="   "):
    o = out.get(v["video_id"])
    tag = f", {o}x their normal" if o and o >= 1.5 else (f", {o}x" if o else "")
    return (f"{indent}- \"{(v.get('title') or '')[:80]}\" {metrics.compact(v.get('views'))} views"
            f"{', short' if int(v.get('is_short') or 0) else ''}{tag}, id={v['video_id']}")


def _focus_block(store, cid):
    ch = store.channel(cid)
    if not ch:
        return ""
    st = json.loads(ch.get("stats_json") or "{}") if ch.get("stats_json") else {}
    vids = store.videos(cid)
    out = metrics.outlier_scores(vids)
    pub = [v for v in vids if v.get("privacy") == "public" and v.get("views")]
    best = sorted(pub, key=lambda v: (out.get(v["video_id"]) or 0, v.get("views") or 0), reverse=True)[:12]
    newest = pub[:8]
    lines = [f"FOCUS CHANNEL: {ch.get('title')} ({ch.get('handle') or cid}) — "
             f"{metrics.compact(st.get('subscribers'))} subs, {metrics.compact(st.get('views'))} lifetime views, "
             f"{st.get('videos') or len(vids)} uploads, {len(pub)} public uploads in the store."]
    card = _style_card(store, cid)
    if card:
        lines.append("Style card: " + card)
    lines.append(" Their biggest outliers (their own proven patterns):")
    lines += [_vid_line(v, out) for v in best]
    lines.append(" Their newest uploads (what they're doing right now):")
    try:
        rank = {r["video_id"]: r for r in metrics.latest_ranking(vids, n=8)}
    except Exception:
        rank = {}
    for v in newest:
        r = rank.get(v["video_id"]) or {}
        vel = f" [{r.get('arrow', '')} {r.get('ratio')}x typical velocity]" if r.get("ratio") else ""
        lines.append(_vid_line(v, out) + vel)
    return "\n".join(lines)


def _network_block(store, focus_id=None, per_channel=3, max_channels=30):
    chans = store.channels()
    chans = [c for c in chans if c["channel_id"] != focus_id]
    chans.sort(key=lambda c: (c.get("stats") or {}).get("subscribers") or 0, reverse=True)
    lines = []
    for c in chans[:max_channels]:
        st = c.get("stats") or {}
        vids = store.videos(c["channel_id"])
        pub = [v for v in vids if v.get("privacy") == "public" and v.get("views")]
        if not pub:
            continue
        out = metrics.outlier_scores(vids)
        ranked = sorted(pub, key=lambda v: (out.get(v["video_id"]) or 0, v.get("views") or 0), reverse=True)
        head = f"{c.get('title') or c['channel_id']}" + (" [linked]" if c.get("has_token") else "")
        if st.get("subscribers"):
            head += f" — {metrics.compact(st['subscribers'])} subs, {metrics.compact(st.get('views'))} views"
        lines.append(head + ":")
        lines += [_vid_line(v, out) for v in ranked[:per_channel]]
    return "\n".join(lines) if lines else "(no channels tracked yet — add some on the Channels tab)"


# ------------------------------------------------------------------------------ memory
def pitched(store):
    p = store.get_setting(PITCHED_KEY)
    return p if isinstance(p, list) else []


def remember_pitched(store, titles, channel_id=None, refs=None):
    """titles: pitched titles; refs: the outlier ids they were converted from (parallel list) -
    both are remembered so the next batch avoids the titles AND the sources."""
    titles = [t.strip() for t in titles if t and t.strip()]
    refs = list(refs or [])
    if not titles and not refs:
        return
    p = pitched(store)
    have = {x.get("title", "").lower() for x in p}
    for i, t in enumerate(titles):
        if t.lower() not in have:
            ref = refs[i] if i < len(refs) and isinstance(refs[i], str) else None
            p.append({"title": t[:140], "channel_id": channel_id, "ts": time.time(), "ref_id": ref or None})
            have.add(t.lower())
    if not titles:
        for r in refs:
            if isinstance(r, str) and r:
                p.append({"title": "", "channel_id": channel_id, "ts": time.time(), "ref_id": r})
    store.set_setting(PITCHED_KEY, p[-MAX_PITCHED:])


def used_refs(store, limit=40):
    """Outlier ids already converted in the most recent pitches - kept OUT of the radar brief so
    a new batch is forced onto new source material."""
    out = []
    for x in reversed(pitched(store)):
        r = x.get("ref_id")
        if r and r not in out:
            out.append(r)
        if len(out) >= limit:
            break
    return out


def forget(store):
    store.set_setting(PITCHED_KEY, [])
    store.set_setting(CHAT_KEY, [])


def history(store):
    h = store.get_setting(CHAT_KEY)
    return h if isinstance(h, list) else []


def _remember_turn(store, role, text):
    h = history(store)
    h.append({"role": role, "text": text, "ts": time.time()})
    store.set_setting(CHAT_KEY, h[-MAX_CHAT:])


def notes(store):
    return store.get_setting(NOTES_KEY) or ""


def set_notes(store, text):
    store.set_setting(NOTES_KEY, (text or "")[:4000])


def _strip_pitched(reply):
    m = _PITCHED_RE.search(reply or "")
    if not m:
        return reply, []
    titles = [t.strip().strip('"').strip() for t in m.group(1).split("|")]
    return reply[: m.start()].rstrip(), [t for t in titles if t]


def _pitched_block(store, limit=80):
    p = [x for x in pitched(store) if x.get("title")][-limit:]
    if not p:
        return "(nothing yet)"
    return "\n".join(f"- {x['title']}" for x in reversed(p))


# ---------------------------------------------------------------------- system prompt
def system_prompt(store, focus_id=None, seed=None):
    net_txt, niche_txt, _ = radar.brief(store, focus_id, seed=seed, exclude_ids=used_refs(store))
    focus = _focus_block(store, focus_id) if focus_id else ""
    own_notes = notes(store)
    return (
        "You are the operator's head of YouTube strategy. Your job: turn REAL outliers into original "
        "video ideas for their niche — packaged (title + thumbnail + hook) and ready to make — plus "
        "sharp answers on packaging, retention, series and channel growth.\n\n"
        "HARD RULES:\n"
        "1. NEVER invent a video, title, creator, view count or id. Every specific you cite comes from the "
        "data blocks below. When you build on a video, put [thumb:VIDEOID] on its own line right after "
        "mentioning it so the app shows the real thumbnail — only ids that appear below. A broken "
        "thumbnail means you made one up. Never do that.\n"
        "2. Work the OUTLIER METHOD out loud but briefly: name the mechanism, the title structure, the "
        "thumbnail beat, why it hit now, then the CONVERSION into our niche. The conversion is the "
        "product: a styled title, a one-line thumbnail concept (subject / emotion / 3-5 words of text / "
        "colors), the first-10-seconds hook, and which real video proves the pattern. Same mechanism, "
        "new subject, better packaging — never a copy of the topic.\n"
        "3. Say the multiples casually and honestly ('this one did ~6x their normal', 'a 40k channel "
        "doing 900k'). Numbers only from the data.\n"
        "4. NEVER repeat or lightly reword anything in ALREADY PITCHED — same opening words, or the same "
        "structure with one noun swapped ('If You See X Run' -> 'If You Hear Y Run'), counts as a repeat. "
        "Outliers already converted are hidden from the radar below on purpose: build every new idea on a "
        "source and a mechanism that is NOT in the pitched list.\n"
        "5. Voice: a sharp friend who lives on YouTube. Short sentences, plain words, contractions. No "
        "corporate filler (leverage, optimize, delve, furthermore), no walls of headers, no restating "
        "the question, no closing offers. Bold titles with **double stars**. Default to 5 ideas when asked "
        "for ideas unless told a number.\n"
        "6. Prefer the FOCUS CHANNEL's voice and format when one is given; otherwise pitch for the network "
        "and say which channel each idea fits.\n"
        "7. END EVERY REPLY with one final line exactly like: [[pitched: Title One | Title Two | ...]] "
        "listing every video title you pitched in this reply (empty brackets [[pitched: ]] if none). "
        "It is stripped before display; it is how you remember.\n\n"
        + (f"=== OPERATOR NOTES (their standing instructions — obey) ===\n{own_notes}\n\n" if own_notes else "")
        + (f"=== {focus} ===\n\n" if focus else "")
        + "=== THE OPERATOR'S TRACKED NETWORK (real, id-carrying) ===\n"
        f"{_network_block(store, focus_id)}\n\n"
        "=== OUTLIER RADAR — INSIDE THE NETWORK (recent videos that beat their own channel's median) ===\n"
        f"{net_txt}\n\n"
        "=== OUTLIER RADAR — THE NICHE BEYOND THE NETWORK (found via the YouTube API; the multiple is "
        "against that channel's own median, so small channels with a real hit rank high) ===\n"
        f"{niche_txt}\n\n"
        "=== ALREADY PITCHED (never repeat these or near-variants) ===\n"
        f"{_pitched_block(store)}\n\n"
        f"=== PLAYBOOK ===\n{PLAYBOOK}"
    )


# ------------------------------------------------------------------------------- chat
def chat(cfg, store, history_in, user_text, images=None, focus_id=None, seed=None):
    """history_in: [{role, text}] prior turns from the client (server memory is merged in when
    the client has none). Returns the reply text with the memory marker stripped."""
    hist = history_in or history(store)
    convo = "\n".join(f"{'You' if h.get('role') in ('assistant', 'model') else 'Operator'}: {h.get('text', '')}"
                      for h in hist[-14:])
    prompt = (system_prompt(store, focus_id, seed=seed)
              + ("\n\n=== CONVERSATION SO FAR ===\n" + convo if convo else "")
              + f"\n\n=== OPERATOR'S MESSAGE ===\n{user_text}\n\nReply as the strategist (remember the final [[pitched: ...]] line):")
    raw = gemini.generate(cfg["gemini_api_key"], prompt, images=images, temperature=1.0, max_tokens=24000)
    reply, titles = _strip_pitched(raw)
    refs = re.findall(r"\[thumb:([A-Za-z0-9_-]{11})\]", reply)
    remember_pitched(store, titles, focus_id, refs=refs)
    _remember_turn(store, "user", user_text)
    _remember_turn(store, "model", reply)
    return reply


# ------------------------------------------------------------------------------ ideas
IDEA_SCHEMA = ('{"ideas":[{"title":"the styled title","channel":"which tracked channel it fits","format":'
               '"short label e.g. challenge / trolling / story / update / ranking / experiment","mechanism":'
               '"one of the nine mechanisms","hook":"the first 10 seconds, one or two sentences, cold open",'
               '"why":"why it should work — cite the real outlier and its multiple casually","ref_id":'
               '"the real VIDEOID it converts (from the data), or empty","ref_pattern":"the pattern taken from '
               'that reference in one line","thumbnail":"one-line thumbnail concept: subject, emotion, prop, '
               'colors","thumb_text":"3-5 words of on-thumbnail text, keyword first","thumbnail_prompt":"a full '
               'art-direction paragraph for an image model: scene, subject, expression, props, lighting, '
               'palette, where the text goes — photoreal unless the niche is a game render","series":"how it '
               'series-ifies if it hits, one line"}]}')


def ideas(cfg, store, channel_id=None, topic="", n=5, seed_video=None, seed=None):
    """Structured pitches (JSON) — the idea board. `seed_video` = a radar/store video id to
    convert specifically. Titles are remembered so the next batch is different."""
    focus = None
    if channel_id:
        ch = store.channel(channel_id)
        focus = ch.get("title") if ch else None
    task = [f"Pitch {n} video ideas" + (f" for {focus}" if focus else " for this network")
            + (f", themed around: {topic}" if topic else "") + "."]
    if seed_video:
        r = radar.find(store, seed_video)
        if r:
            task.append(f"Every idea must CONVERT this specific outlier: \"{r['title']}\" by {r.get('channel')} "
                        f"({r['outlier']}x their normal, {metrics.compact(r.get('views'))} views, id={r['video_id']}). "
                        "Give it different angles: the same mechanism with our subject, a bigger ladder, a versus "
                        "version, a story version, a Shorts cut. ref_id must be that id.")
    task.append("Each idea converts a DIFFERENT real outlier or mechanism from the radar/network — no two ideas "
                "from the same reference, none from ALREADY PITCHED, and each must read like this channel's own "
                "voice. Make the thumbnail_prompt concrete enough that an artist could paint it without asking.")
    task.append("Return JSON ONLY in exactly this shape: " + IDEA_SCHEMA)
    prompt = system_prompt(store, channel_id, seed=seed) + "\n\n=== TASK ===\n" + "\n".join(task)
    j = gemini.generate(cfg["gemini_api_key"], prompt, json_mode=True, temperature=1.0, max_tokens=24000)
    out = j.get("ideas", j if isinstance(j, list) else []) if isinstance(j, (dict, list)) else []
    out = [i for i in (out if isinstance(out, list) else []) if isinstance(i, dict) and i.get("title")]
    for i in out:   # the app renders only ids it can trust
        rid = (i.get("ref_id") or "").strip()
        i["ref_id"] = rid if re.fullmatch(r"[A-Za-z0-9_-]{11}", rid) and _known_id(store, rid) else ""
    remember_pitched(store, [i["title"] for i in out], channel_id, refs=[i.get("ref_id") or seed_video for i in out])
    return {"ideas": out, "channel_id": channel_id, "topic": topic, "seed_video": seed_video}


def _known_id(store, vid):
    if store.video(vid):
        return True
    d = radar.latest(store) or {}
    return any(r.get("video_id") == vid for key in ("niche", "network") for r in (d.get(key) or []))


def outlier_report(cfg, store, channel_id=None, n=8, seed=None):
    """'What's hitting right now and why' — a read of the radar, no pitches. JSON."""
    prompt = (system_prompt(store, channel_id, seed=seed)
              + f"\n\n=== TASK ===\nPick the {n} most instructive outliers across BOTH radar feeds (prefer the niche "
              "feed, mix mechanisms, mix Shorts and longform). For each: the mechanism, why it hit (title structure, "
              "thumbnail beat, timing), and a one-line 'how we'd convert it'. Then 3 trends you see across them.\n"
              'Return JSON ONLY: {"picks":[{"video_id":"real id","title":"","channel":"","multiple":"e.g. 6.2x",'
              '"mechanism":"","why":"","convert":""}],"trends":["",""]}')
    j = gemini.generate(cfg["gemini_api_key"], prompt, json_mode=True, temperature=0.8, max_tokens=16000)
    picks = [p for p in (j.get("picks") or []) if isinstance(p, dict)
             and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(p.get("video_id") or "")) and _known_id(store, p["video_id"])]
    return {"picks": picks, "trends": j.get("trends") or []}
