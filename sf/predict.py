"""Video performance prediction: an AI-modelled audience-retention (AVD) curve for a
video BEFORE it has analytics — grounded in real data wherever real data exists.

Two modes:
  • FAST  — from a YouTube link/ID (metadata + the REAL transcript via captions) or a
            pasted script.
  • WATCH — from an actual MP4 (uploaded or a local path): ffmpeg samples frames + audio
            metrics, faster-whisper transcribes the speech (GPU when available), then
            Gemini VISION does a blunt, timestamped editor teardown on top of the
            retention prediction, and pitches titles + thumbnail concepts from what it saw.

Grounding (what turns a guess into a statistic):
  • TRANSCRIPT with timestamps — yt-dlp captions (android client; YouTube's public
    timedtext endpoint is gated for most videos now), else timedtext, else whisper.
  • CHANNEL BASELINE — when the video belongs to a tracked channel: median views and
    length of its previous 30 same-format uploads, this video's multiple of that median.
  • ACTUAL RETENTION — when the channel is linked with the analytics scope, YouTube's
    own audienceWatchRatio curve + averageViewPercentage for the video (and the
    channel's typical AVD%) are fetched and drawn next to the prediction.

It's a prediction/heuristic, labelled as such. Frame/audio sampling needs ffmpeg on PATH.
"""
import glob
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

from . import gemini, http

_VID = re.compile(r"(?:v=|/shorts/|/embed/|/live/|youtu\.be/|/v/)([A-Za-z0-9_-]{11})|^([A-Za-z0-9_-]{11})$")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# clip-scout's vendored faster-whisper (+ CUDA DLLs) and its subprocess worker are reused
# for WATCH mode; both are optional — without them the MP4 review runs on frames + audio only.
CLIPSCOUT = os.environ.get("CLIPSCOUT_DIR") or r"D:\Claude\clip-scout"
WHISPER_PY = os.environ.get("CLIPSCOUT_PY") or (r"C:\Python314\python.exe" if os.path.exists(r"C:\Python314\python.exe") else sys.executable)
WHISPER_MODEL_GPU = "large-v3-turbo"
WHISPER_MODEL_CPU = "base.en"
CAPTION_LANGS = ("en", "en-US", "en-GB", "en-orig")


def parse_video_id(s):
    s = (s or "").strip()
    m = _VID.search(s)
    return (m.group(1) or m.group(2)) if m else None


def _fmt_dur(s):
    s = int(s or 0)
    return (f"{s // 3600}h" if s >= 3600 else "") + f"{(s % 3600) // 60}m{s % 60:02d}s"


def _mmss(s):
    s = int(s or 0)
    return f"{s // 60}:{s % 60:02d}"


def _run(cmd, timeout=600, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace",
                          env=env, creationflags=CREATE_NO_WINDOW)


# ------------------------------------------------------------------ transcripts
def _clean_env():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _ytdlp():
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    pylib = os.path.join(CLIPSCOUT, "pylib")
    if os.path.isdir(os.path.join(pylib, "yt_dlp")):
        return [WHISPER_PY, "-m", "yt_dlp"]
    return None


def _unescape(t):
    return (t.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
             .replace("&lt;", "<").replace("&gt;", ">").replace("\u200b", ""))


def parse_json3(text):
    """yt-dlp json3 caption file -> [(start_s, end_s, text)]."""
    try:
        j = json.loads(text)
    except Exception:
        return []
    out = []
    for ev in j.get("events") or []:
        segs = ev.get("segs") or []
        s = "".join(x.get("utf8", "") for x in segs).replace("\n", " ").strip()
        if not s or ev.get("tStartMs") is None:
            continue
        st = ev["tStartMs"] / 1000.0
        out.append((st, st + (ev.get("dDurationMs") or 0) / 1000.0, re.sub(r"\s+", " ", s)))
    return out


def parse_timedtext(text):
    out = []
    for m in re.finditer(r'<text start="([\d.]+)"(?: dur="([\d.]+)")?[^>]*>(.*?)</text>', text, re.S):
        s = re.sub(r"\s+", " ", _unescape(re.sub(r"<[^>]+>", " ", m.group(3)))).strip()
        if s:
            st = float(m.group(1))
            out.append((st, st + float(m.group(2) or 0), s))
    return out


def _captions_ytdlp(vid, workdir):
    cmd = _ytdlp()
    if not cmd:
        return []
    args = cmd + ["--skip-download", "--write-sub", "--write-auto-sub", "--sub-lang", ",".join(CAPTION_LANGS),
                  "--sub-format", "json3", "--no-warnings", "--no-progress", "--no-playlist",
                  "-o", os.path.join(workdir, "%(id)s"), f"https://www.youtube.com/watch?v={vid}"]
    for extra in (["--extractor-args", "youtube:player_client=android"], []):
        try:
            _run(args + extra, timeout=120, env=_clean_env())
        except (subprocess.TimeoutExpired, OSError):
            continue
        for lang in CAPTION_LANGS:            # manual English first, auto-generated last
            for fp in glob.glob(os.path.join(workdir, f"{vid}.{lang}.json3")):
                try:
                    with open(fp, encoding="utf-8") as f:
                        segs = parse_json3(f.read())
                except OSError:
                    continue
                if len(segs) >= 3:
                    return segs
    return []


def _captions_timedtext(vid):
    for lang in ("en", "en-US", "en-GB"):
        for url in (f"https://www.youtube.com/api/timedtext?lang={lang}&v={vid}",
                    f"https://video.google.com/timedtext?lang={lang}&v={vid}"):
            try:
                body, _ = http.request(url, timeout=15, retries=0)
                segs = parse_timedtext(body.decode("utf-8", "replace"))
            except Exception:
                continue
            if len(segs) >= 3:
                return segs
    return []


def fetch_transcript(vid, cache_dir=None):
    """Timestamped transcript for a YouTube video: {"segments": [[start, end, text]...],
    "source": "captions"|"timedtext"|""}. Cached per video once found."""
    cache = os.path.join(cache_dir, f"{vid}.json") if cache_dir else None
    if cache and os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                j = json.load(f)
            if j.get("segments"):
                return j
        except Exception:
            pass
    workdir = tempfile.mkdtemp(prefix="sfcap_")
    try:
        segs, source = _captions_ytdlp(vid, workdir), "captions"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if not segs:
        segs, source = _captions_timedtext(vid), "timedtext"
    out = {"segments": [[round(a, 2), round(b, 2), t] for a, b, t in segs], "source": source if segs else ""}
    if segs and cache:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(out, f)
        except OSError:
            pass
    return out


def fetch_captions(vid, langs=None):
    """Back-compat: plain caption text ("" if none)."""
    return " ".join(s[2] for s in fetch_transcript(vid)["segments"])[:8000]


def transcript_block(segments, cap_chars=9000, chunk_s=15.0):
    """Render [m:ss] lines, one per ~15 s of speech. If it doesn't fit, keep the first
    minute dense (the hook is what retention hinges on) and thin the rest evenly."""
    lines, cur, cur_t = [], [], None
    for seg in segments or []:
        st, txt = float(seg[0]), str(seg[2]).strip()
        if not txt:
            continue
        if cur_t is None:
            cur_t = st
        if st - cur_t >= chunk_s and cur:
            lines.append((cur_t, " ".join(cur)))
            cur, cur_t = [], st
        cur.append(txt)
    if cur:
        lines.append((cur_t or 0, " ".join(cur)))
    if not lines:
        return ""
    total = sum(len(t) + 8 for _, t in lines)
    if total > cap_chars:
        head = [l for l in lines if l[0] < 60]
        rest = [l for l in lines if l[0] >= 60]
        budget = max(0, cap_chars - sum(len(t) + 8 for _, t in head))
        if rest and budget:
            keep = max(1, int(len(rest) * budget / max(1, sum(len(t) + 8 for _, t in rest))))
            step = max(1, len(rest) // keep)
            rest = rest[::step]
        else:
            rest = []
        lines = head + rest
    return "\n".join(f"[{_mmss(t)}] {txt}" for t, txt in lines)[:cap_chars + 400]


# ------------------------------------------------------------------ whisper (WATCH mode)
def _cuda_dll_dirs():
    pylib = os.path.join(CLIPSCOUT, "pylib")
    cands = [os.path.join(pylib, "nvidia", "cublas", "bin"), os.path.join(pylib, "nvidia", "cudnn", "bin")]
    return [d for d in cands if os.path.isdir(d)]


def whisper_available():
    return (os.path.exists(os.path.join(CLIPSCOUT, "tx_worker.py"))
            and os.path.isdir(os.path.join(CLIPSCOUT, "pylib", "faster_whisper"))
            and os.path.exists(WHISPER_PY))


def transcribe_file(path, dur, workdir, log=None):
    """Speech -> [(start, end, text)] via clip-scout's faster-whisper worker in a subprocess
    (GPU first, CPU fallback, both with timeouts so a CUDA hang can't take the server down)."""
    if not whisper_available():
        return [], ""
    wav = os.path.join(workdir, "speech.wav")
    try:
        _run(["ffmpeg", "-loglevel", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav, "-y"],
             timeout=600)
    except (subprocess.TimeoutExpired, OSError):
        return [], ""
    if not os.path.exists(wav):
        return [], ""
    env = _clean_env()
    env["PYTHONPATH"] = os.path.join(CLIPSCOUT, "pylib")
    env["CLIPSCOUT_DLL_DIRS"] = os.pathsep.join(_cuda_dll_dirs())
    worker = os.path.join(CLIPSCOUT, "tx_worker.py")
    cap = str(int(max(dur, 60)))
    for model, device, compute, timeout in ((WHISPER_MODEL_GPU, "cuda", "float16", 180 + dur * 0.6),
                                            (WHISPER_MODEL_CPU, "cpu", "int8", 120 + dur * 0.8)):
        outp = os.path.join(workdir, f"tx_{device}.json")
        t0 = time.time()
        try:
            r = _run([WHISPER_PY, worker, wav, model, device, compute, cap, outp], timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            if log:
                log(f"whisper {device} timed out")
            continue
        except OSError:
            continue
        if r.returncode == 0 and os.path.exists(outp):
            try:
                with open(outp, encoding="utf-8") as f:
                    j = json.load(f)
            except Exception:
                continue
            segs = [(s["start"], s["end"], s["text"]) for s in j.get("segments", []) if s.get("text")]
            if log:
                log(f"whisper {model}/{device}: {len(segs)} segments in {time.time() - t0:.0f}s")
            if segs:
                return segs, f"whisper {model}"
    return [], ""


# ------------------------------------------------------------------ ffmpeg (WATCH mode)
def _probe_duration(path):
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _extract_frames(path, dur, workdir, count=14):
    """6 frames across the hook (first ~40% or 60s), the rest spread over the body."""
    hook_end = min(60.0, dur * 0.4) if dur else 60.0
    times = [2 + i * (hook_end - 2) / 5 for i in range(6)]
    if dur > hook_end + 10:
        rest = count - 6
        times += [hook_end + (i + 1) * (dur - hook_end - 3) / rest for i in range(rest)]
    frames = []
    for i, t in enumerate(times):
        fp = os.path.join(workdir, f"f{i:02d}.jpg")
        _run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
              "-vf", "scale=640:-2", "-q:v", "5", fp, "-y"], timeout=120)
        if os.path.exists(fp):
            frames.append((t, fp))
    return frames


def _audio_metrics(path, dur):
    m = {}
    r = _run(["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", "-"], timeout=600)
    for k, pat in (("mean_volume_db", r"mean_volume: ([-\d.]+) dB"), ("max_volume_db", r"max_volume: ([-\d.]+) dB")):
        mm = re.search(pat, r.stderr)
        if mm:
            m[k] = float(mm.group(1))
    r = _run(["ffmpeg", "-i", path, "-af", "silencedetect=noise=-32dB:d=1.2", "-f", "null", "-"], timeout=600)
    sil = re.findall(r"silence_duration: ([\d.]+)", r.stderr)
    m["silences_over_1.2s"] = len(sil)
    m["total_silence_s"] = round(sum(float(s) for s in sil), 1)
    seg = min(dur or 240, 240)
    r = _run(["ffmpeg", "-t", str(seg), "-i", path, "-vf", "select='gt(scene,0.35)',metadata=print", "-an", "-f", "null", "-"], timeout=600)
    m["est_cuts_per_min"] = round(len(re.findall(r"scene_score", r.stderr)) / (seg / 60), 1) if seg else 0
    return m


def ffmpeg_available():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


# ------------------------------------------------------------------ channel grounding
def channel_baseline(store, channel_id, video_id=None, is_short=0, window=30):
    """Median views / length of the channel's previous `window` public same-format uploads
    (from the synced videos table) and this video's multiple of that median. None when the
    channel isn't tracked or has too little history."""
    if not store or not channel_id:
        return None
    try:
        ch = store.channel(channel_id)
        vids = store.videos(channel_id) if ch else []
    except Exception:
        return None
    if not ch or not vids:
        return None
    fmt = int(is_short or 0)
    same = [v for v in vids if v.get("privacy", "public") == "public" and int(v.get("is_short") or 0) == fmt
            and v.get("views") is not None and v.get("video_id") != video_id]
    same.sort(key=lambda v: v.get("published_at") or "", reverse=True)
    this = next((v for v in vids if v.get("video_id") == video_id), None) if video_id else None
    if this and this.get("published_at"):
        prev = [v for v in same if (v.get("published_at") or "") < this["published_at"]] or same
    else:
        prev = same
    prev = prev[:window]
    if len(prev) < 3:
        return None
    views = [int(v["views"]) for v in prev]
    durs = [int(v["duration_s"]) for v in prev if v.get("duration_s")]
    out = {"channel_id": channel_id, "channel_title": ch.get("title"), "format": "short" if fmt else "long",
           "n": len(prev), "median_views": int(statistics.median(views)), "top_views": max(views),
           "median_duration_s": int(statistics.median(durs)) if durs else None}
    if this and this.get("views"):
        out["this_views"] = int(this["views"])
        out["this_multiple"] = round(int(this["views"]) / out["median_views"], 2) if out["median_views"] else None
    return out


def actual_retention(store, tokens, channel_id, video_id, analytics_cls=None):
    """YouTube's own retention curve + AVD% for an OWN video (analytics scope required)
    and the channel's typical AVD% across its top uploads. None when unavailable."""
    if not (store and tokens and channel_id and video_id):
        return None
    try:
        ch = store.channel(channel_id)
        if not ch or "analytics" not in (ch.get("scopes") or "") or not ch.get("refresh_token"):
            return None
        from . import api_analytics
        an = (analytics_cls or api_analytics.Analytics)(tokens.get(ch))
        ids = f"channel=={channel_id}"
        r = an.query("2015-01-01", time.strftime("%Y-%m-%d"), ["audienceWatchRatio"],
                     dimensions=["elapsedVideoTimeRatio"], filters=f"video=={video_id}", ids=ids)
        curve = []
        for row in r["rows"]:
            try:
                curve.append({"pct": round(float(row["elapsedVideoTimeRatio"]) * 100),
                              "retention": round(min(150, float(row["audienceWatchRatio"]) * 100), 1)})
            except Exception:
                continue
        if len(curve) < 5:
            return None
        out = {"curve": curve}
        try:
            r2 = an.query("2015-01-01", time.strftime("%Y-%m-%d"), ["views", "averageViewPercentage"],
                          dimensions=["video"], sort="-views", max_results=50, ids=ids)
            rows = r2["rows"]
            mine = next((x for x in rows if x.get("video") == video_id), None)
            if mine:
                out["avd_pct"] = round(float(mine["averageViewPercentage"]), 1)
                out["views"] = int(mine.get("views") or 0)
            avps = [float(x["averageViewPercentage"]) for x in rows if x.get("averageViewPercentage") is not None]
            if len(avps) >= 3:
                out["channel_avd_pct"] = round(statistics.median(avps), 1)
        except Exception:
            pass
        return out
    except Exception:
        return None


# ------------------------------------------------------------------ shared normalisation
def _thumb_str(t):
    if isinstance(t, dict):
        parts = [str(t.get(k)) for k in ("subject", "text", "colours", "colors", "layout") if t.get(k)]
        return " — ".join(parts) if parts else json.dumps(t)
    return str(t)


def _finalize(pred, video, title, dur, has_transcript=False, watched=False, transcript_source="",
              baseline=None, actual=None):
    if not isinstance(pred, dict):
        pred = {}
    curve = []
    for p in pred.get("curve", []) or []:
        try:
            pct = max(0, min(100, float(p.get("pct"))))
            ret = max(0, min(100, float(p.get("retention"))))
            curve.append({"pct": round(pct), "retention": round(ret), "t": int(dur * pct / 100) if dur else None})
        except Exception:
            continue
    curve.sort(key=lambda c: c["pct"])
    dedup = {}
    for c in curve:
        dedup[c["pct"]] = c
    curve = [dedup[k] for k in sorted(dedup)]
    if not curve or curve[0]["pct"] > 0:
        curve.insert(0, {"pct": 0, "retention": 100, "t": 0})
    else:
        curve[0]["retention"] = 100
    if len(curve) < 2:
        curve.append({"pct": 100, "retention": 35, "t": int(dur) if dur else None})
    pred["curve"] = curve
    for k in ("drop_offs", "replays"):
        items = [m for m in (pred.get(k) or []) if isinstance(m, dict)]
        for m in items:
            try:
                m["t"] = int(dur * float(m.get("pct", 0)) / 100) if dur else None
            except Exception:
                m["t"] = None
        pred[k] = items
    # editor notes carry an absolute time; keep them in order
    notes = []
    for n in pred.get("notes", []) or []:
        if not isinstance(n, dict):
            continue
        try:
            t = float(n.get("t")) if n.get("t") is not None else (dur * float(n.get("pct", 0)) / 100 if dur else None)
        except Exception:
            t = None
        n["t"] = int(t) if t is not None else None
        n["ts"] = _mmss(t) if t is not None else "—"
        notes.append(n)
    notes.sort(key=lambda n: (n["t"] is None, n["t"] or 0))
    pred["notes"] = notes
    pk = pred.get("packaging")
    if isinstance(pk, dict):
        pk["titles"] = [str(t) for t in (pk.get("titles") or []) if t]
        th = pk.get("thumbnails") or ([pk["thumbnail"]] if pk.get("thumbnail") else [])
        pk["thumbnails"] = [_thumb_str(t) for t in th if t]
    else:
        pred["packaging"] = {"titles": [], "thumbnails": []}
    pred["fixes"] = [str(f) for f in (pred.get("fixes") or []) if f]
    for k in ("hook_score", "avd_pct", "predicted_score"):
        try:
            pred[k] = round(float(pred[k]), 1) if pred.get(k) is not None else None
        except Exception:
            pred[k] = None
    conf = str(pred.get("confidence") or "").lower()
    pred["confidence"] = conf if conf in ("low", "medium", "high") else ("high" if watched else ("medium" if has_transcript else "low"))
    pred["has_transcript"] = has_transcript
    pred["transcript_source"] = transcript_source
    pred["watched"] = watched
    pred["baseline"] = baseline
    pred["actual"] = actual
    # expected views = the model's multiple of the channel's median (only with a baseline)
    vm = pred.pop("views_multiple", None)
    pred["expected_views"] = None
    if baseline and baseline.get("median_views"):
        try:
            lo, hi = float(vm[0]), float(vm[1])
            lo, hi = max(0.05, min(lo, hi)), min(50, max(lo, hi))
            pred["expected_views"] = {"low": int(baseline["median_views"] * lo), "high": int(baseline["median_views"] * hi),
                                      "multiple": [round(lo, 2), round(hi, 2)]}
        except Exception:
            pass
    return {"video": video, "title": title, "duration_s": dur, "prediction": pred}


_JSON_SHAPE = ('{"hook_score":0-10,"avd_pct":0-100,"predicted_score":0-100,"confidence":"low"|"medium"|"high",'
               '"verdict":"short phrase",'
               '"curve":[{"pct":0,"retention":100},{"pct":5,"retention":..}, ... one point EVERY 5% through 100 (21 points)],'
               '"drop_offs":[{"pct":int,"reason":"why they leave, quoting the moment"} up to 4, worst first],'
               '"replays":[{"pct":int,"reason":"why rewatched","strength":1-3} up to 4],'
               '"notes":[{"t":seconds,"type":"cut"|"music"|"sfx"|"pacing"|"visual"|"hook"|"praise","note":"blunt specific instruction"} 8-16 in time order],'
               '"summary":"2 blunt sentences","fixes":["3-5 concrete edits, most impactful first"],'
               '"views_multiple":[low,high] (expected views as a multiple of the channel median, e.g. [0.6,1.8]; only when a CHANNEL BASELINE is given, else null),'
               '"packaging":{"titles":["5 stronger title options"],"thumbnails":["3 concepts as plain strings: subject, text, colours"]}}')

_CRITIC = ("You are a BRUTALLY honest YouTube editor and retention analyst doing a pre-release teardown. "
           "Be specific and timestamped like a top editor: name scenes that add nothing ('cut 1:12-1:40, it kills pace'), "
           "where dramatic/tension music should hit, awkward or missing SFX, dead air, weak hooks, and the moments worth "
           "keeping (praise only what earns it). No fluff, no hedging. Timestamps and quotes must come from the "
           "transcript/frames you were given — never invent moments.")

_CURVE_RULES = ("Model an audience-retention (AVD) curve like YouTube's: x = percent through the video (0-100), "
                "y = percent still watching. It starts at 100, drops hardest in the first 30 seconds (typical: 20-35% "
                "of viewers gone by 0:30 on long-form, 30-50% on Shorts), then declines gradually; small bumps are "
                "allowed at genuinely strong or rewatched moments; it ends near the AVD%-implied tail. avd_pct must be "
                "consistent with the curve (the average of the curve ≈ avd_pct). Calibrate against the CHANNEL "
                "BASELINE / TYPICAL AVD when given rather than generic averages.")


def _baseline_block(baseline, actual):
    if not baseline and not actual:
        return ""
    lines = []
    if baseline:
        lines.append(f"CHANNEL BASELINE ({baseline.get('channel_title') or baseline['channel_id']}, {baseline['format']}-form, "
                     f"previous {baseline['n']} uploads): median views {baseline['median_views']:,}, best {baseline['top_views']:,}"
                     + (f", median length {_fmt_dur(baseline['median_duration_s'])}" if baseline.get("median_duration_s") else "")
                     + (f". This video so far: {baseline['this_views']:,} views = {baseline['this_multiple']}x the median."
                        if baseline.get("this_views") else "."))
    if actual and actual.get("channel_avd_pct"):
        lines.append(f"TYPICAL AVD on this channel (YouTube Analytics, median of top uploads): {actual['channel_avd_pct']}%.")
    return "\n".join(lines) + "\n"


def predict(cfg, yt=None, source="", script="", title="", duration_s=0, store=None, tokens=None):
    """FAST mode: URL/id (metadata + the real transcript when captions exist) or a pasted script."""
    video, vid, segs, tsource = {}, (parse_video_id(source) if source else None), [], ""
    channel_id, is_short = None, 0
    if vid and yt is not None:
        try:
            rows = yt.videos([vid])
            if rows:
                v = rows[0]
                video = {"video_id": vid, "title": v.get("title"), "duration_s": v.get("duration_s"),
                         "thumb": v.get("thumb") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                         "views": v.get("views"), "is_short": int(v.get("is_short") or 0),
                         "channel_title": v.get("channel_title"), "channel_id": v.get("channel_id"),
                         "published_at": v.get("published_at")}
                title = title or v.get("title") or ""
                duration_s = duration_s or v.get("duration_s") or 0
                channel_id, is_short = v.get("channel_id"), video["is_short"]
        except Exception:
            pass
    if vid and store and not channel_id:
        try:
            row = store.video(vid)
            if row:
                channel_id, is_short = row.get("channel_id"), int(row.get("is_short") or 0)
                title = title or row.get("title") or ""
                duration_s = duration_s or row.get("duration_s") or 0
                video = video or {"video_id": vid, "title": row.get("title"), "duration_s": row.get("duration_s"),
                                  "thumb": row.get("thumb") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                                  "views": row.get("views"), "is_short": is_short, "channel_id": channel_id}
        except Exception:
            pass
    if vid and not video:
        video = {"video_id": vid, "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}
    if vid and not script.strip():
        cache_dir = os.path.join(os.path.dirname(cfg.get("database") or "data/x"), "captions")
        tx = fetch_transcript(vid, cache_dir)
        segs, tsource = tx.get("segments") or [], tx.get("source") or ""
    if script.strip():
        content, tsource = script.strip()[:9000], "script"
    else:
        content = transcript_block(segs)
    if not (title or content):
        from .tools import ToolError
        raise ToolError("Give a YouTube video link/ID, paste the script, or upload/point to an MP4 to analyze.")
    baseline = channel_baseline(store, channel_id, vid, is_short) if channel_id else None
    actual = actual_retention(store, tokens, channel_id, vid) if (channel_id and vid) else None
    if content and tsource != "script":
        have = "the REAL transcript with [m:ss] timestamps (use them for every note and drop-off)"
    elif content:
        have = "the creator's script/outline (timestamps are estimates from reading pace)"
    else:
        have = "only the title + length (no transcript — lower confidence, say so)"
    prompt = (f"{_CRITIC}\nYou have {have}.\nTITLE: {title or '(none)'}\nLENGTH: {_fmt_dur(duration_s)} "
              f"({int(duration_s or 0)}s){' — a SHORT' if is_short else ''}\n"
              + _baseline_block(baseline, actual)
              + f"CONTENT:\n<<<{(content or '(none)')}>>>\n\n"
              + _CURVE_RULES + " Then the biggest DROP-OFFS, the most-REPLAYED moments, and blunt timestamped editor "
              "NOTES.\nReturn JSON ONLY: " + _JSON_SHAPE)
    pred = gemini.generate(cfg["gemini_api_key"], prompt, json_mode=True, temperature=0.45, max_tokens=12000)
    return _finalize(pred, video, title, duration_s, has_transcript=bool(content), watched=False,
                     transcript_source=tsource, baseline=baseline, actual=actual)


def predict_video(cfg, path, title="", transcribe=True, log=None):
    """WATCH mode: analyze an actual MP4 at `path` (local file or an uploaded temp)."""
    from .tools import ToolError
    if not path or not os.path.exists(path):
        raise ToolError("Video file not found: " + str(path))
    if not ffmpeg_available():
        raise ToolError("ffmpeg is not available on the server, so the video can't be watched. Paste the script instead.")
    dur = _probe_duration(path)
    workdir = tempfile.mkdtemp(prefix="sfpredict_")
    try:
        frames = _extract_frames(path, dur, workdir)
        if not frames:
            raise ToolError("Couldn't read frames from that video (unsupported/corrupt file?).")
        audio = _audio_metrics(path, dur)
        segs, tsource = ([], "")
        if transcribe:
            segs, tsource = transcribe_file(path, dur, workdir, log=log)
        imgs = []
        for _, fp in frames:
            try:
                with open(fp, "rb") as f:
                    imgs.append((f.read(), "image/jpeg"))
            except Exception:
                pass
        tlabels = ", ".join(_mmss(t) for t, _ in frames)
        speech = transcript_block([(a, b, t) for a, b, t in segs])
        prompt = (f"{_CRITIC}\nYou are shown {len(imgs)} frames in time order sampled at: {tlabels}.\n"
                  f"TITLE: {title or os.path.basename(path)}\nLENGTH: {_fmt_dur(dur)} ({int(dur)}s)\n"
                  f"AUDIO METRICS: {json.dumps(audio)}\n"
                  + (f"SPEECH TRANSCRIPT ({tsource}, [m:ss] timestamps):\n<<<{speech}>>>\n" if speech else
                     "SPEECH TRANSCRIPT: none available (judge from frames + audio metrics only).\n")
                  + "\nJudge the hook, pacing (cuts/min vs the audio metric), dead air (silences), what is SAID vs what is "
                  "SHOWN, the visuals in the frames, and packaging. " + _CURVE_RULES + " Give blunt timestamped editor "
                  "NOTES tied to what you SEE/HEAR/measure. Then pitch stronger titles and rough thumbnail concepts based "
                  "on the actual footage.\nReturn JSON ONLY: " + _JSON_SHAPE)
        pred = gemini.generate(cfg["gemini_api_key"], prompt, images=imgs, json_mode=True, temperature=0.5, max_tokens=12000)
        video = {"title": title or os.path.basename(path), "duration_s": dur, "audio": audio,
                 "speech_segments": len(segs)}
        return _finalize(pred, video, title or os.path.basename(path), dur, has_transcript=bool(segs), watched=True,
                         transcript_source=tsource or "frames + audio")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
