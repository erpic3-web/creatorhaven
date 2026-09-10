"""Video performance prediction: an AI-modelled audience-retention (AVD) curve for a
video BEFORE it has analytics.

Two modes:
  • FAST  — from title + length + (best-effort) transcript, or a pasted script.
  • WATCH — from an actual MP4 (uploaded or a local path): ffmpeg samples frames + audio
            metrics, then Gemini VISION does a blunt, timestamped editor teardown
            ("cut this scene", "dramatic music here", "awkward SFX at 1:20") on top of the
            retention prediction, and pitches titles + thumbnail concepts from what it saw.

It's a prediction/heuristic, labelled as such. Frame/audio sampling needs ffmpeg on PATH.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

from . import gemini, http

_VID = re.compile(r"(?:v=|/shorts/|/embed/|/live/|youtu\.be/|/v/)([A-Za-z0-9_-]{11})|^([A-Za-z0-9_-]{11})$")


def parse_video_id(s):
    s = (s or "").strip()
    m = _VID.search(s)
    return (m.group(1) or m.group(2)) if m else None


def fetch_captions(vid, langs=("en", "en-US", "en-GB")):
    """Best-effort public caption text via timedtext. "" if none (auto-captions are often gated)."""
    for lang in langs:
        for url in (f"https://www.youtube.com/api/timedtext?lang={lang}&v={vid}",
                    f"https://video.google.com/timedtext?lang={lang}&v={vid}"):
            try:
                body, _ = http.request(url, timeout=15, retries=0)
                text = body.decode("utf-8", "replace")
            except Exception:
                continue
            if "<text" in text:
                out = re.sub(r"<[^>]+>", " ", " ".join(re.findall(r"<text[^>]*>(.*?)</text>", text, re.S)))
                out = (out.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
                       .replace("&lt;", "<").replace("&gt;", ">"))
                out = re.sub(r"\s+", " ", out).strip()
                if len(out) > 40:
                    return out[:8000]
    return ""


def _fmt_dur(s):
    s = int(s or 0)
    return (f"{s // 3600}h" if s >= 3600 else "") + f"{(s % 3600) // 60}m{s % 60:02d}s"


def _mmss(s):
    s = int(s or 0)
    return f"{s // 60}:{s % 60:02d}"


# ------------------------------------------------------------------ ffmpeg (WATCH mode)
def _run(cmd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")


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


# ------------------------------------------------------------------ shared normalisation
def _finalize(pred, video, title, dur, has_transcript=False, watched=False):
    curve = []
    for p in pred.get("curve", []):
        try:
            pct = max(0, min(100, float(p.get("pct"))))
            ret = max(0, min(100, float(p.get("retention"))))
            curve.append({"pct": round(pct), "retention": round(ret), "t": int(dur * pct / 100) if dur else None})
        except Exception:
            continue
    curve.sort(key=lambda c: c["pct"])
    pred["curve"] = curve or [{"pct": 0, "retention": 100, "t": 0}, {"pct": 100, "retention": 35, "t": dur}]
    for k in ("drop_offs", "replays"):
        for m in pred.get(k, []):
            try:
                m["t"] = int(dur * float(m.get("pct", 0)) / 100) if dur else None
            except Exception:
                m["t"] = None
    # editor notes carry an absolute time; keep them in order
    notes = []
    for n in pred.get("notes", []):
        try:
            t = float(n.get("t")) if n.get("t") is not None else (dur * float(n.get("pct", 0)) / 100 if dur else None)
        except Exception:
            t = None
        n["t"] = int(t) if t is not None else None
        n["ts"] = _mmss(t) if t is not None else "—"
        notes.append(n)
    notes.sort(key=lambda n: (n["t"] is None, n["t"] or 0))
    pred["notes"] = notes
    pred["has_transcript"] = has_transcript
    pred["watched"] = watched
    return {"video": video, "title": title, "duration_s": dur, "prediction": pred}


_JSON_SHAPE = ('{"hook_score":0-10,"avd_pct":0-100,"predicted_score":0-100,"verdict":"short phrase",'
               '"curve":[{"pct":0,"retention":100}, ~12 points across 0..100],'
               '"drop_offs":[{"pct":int,"reason":"why they leave"} up to 4, worst first],'
               '"replays":[{"pct":int,"reason":"why rewatched","strength":1-3} up to 4],'
               '"notes":[{"t":seconds,"type":"cut"|"music"|"sfx"|"pacing"|"visual"|"hook"|"praise","note":"blunt specific instruction"} 8-16 in time order],'
               '"summary":"2 blunt sentences","fixes":["3-5 concrete edits, most impactful first"],'
               '"packaging":{"titles":["5 stronger title options"],"thumbnails":["3 concepts: subject, text, colours"]}}')

_CRITIC = ("You are a BRUTALLY honest YouTube editor and retention analyst doing a pre-release teardown. "
           "Be specific and timestamped like a top editor: name scenes that add nothing ('cut 1:12-1:40, it kills pace'), "
           "where dramatic/tension music should hit, awkward or missing SFX, dead air, weak hooks, and the moments worth "
           "keeping (praise only what earns it). No fluff, no hedging.")


def predict(cfg, yt=None, source="", script="", title="", duration_s=0):
    """FAST mode: URL/id (metadata + best-effort captions) or a pasted script."""
    video, vid, transcript = {}, (parse_video_id(source) if source else None), ""
    if vid and yt is not None:
        try:
            rows = yt.videos([vid])
            if rows:
                v = rows[0]
                video = {"video_id": vid, "title": v.get("title"), "duration_s": v.get("duration_s"),
                         "thumb": v.get("thumb") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                         "views": v.get("views"), "is_short": int(v.get("is_short") or 0),
                         "channel_title": v.get("channel_title"), "published_at": v.get("published_at")}
                title = title or v.get("title") or ""
                duration_s = duration_s or v.get("duration_s") or 0
        except Exception:
            pass
        transcript = fetch_captions(vid)
    if vid and not video:
        video = {"video_id": vid, "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}
    content = script.strip() or transcript
    if not (title or content):
        from .tools import ToolError
        raise ToolError("Give a YouTube video link/ID, paste the script, or upload/point to an MP4 to analyze.")
    have = "the full transcript/script" if content else "only the title + length (no transcript — lower confidence)"
    prompt = (f"{_CRITIC}\nYou have {have}.\nTITLE: {title or '(none)'}\nLENGTH: {_fmt_dur(duration_s)} "
              f"({int(duration_s or 0)}s){' — a SHORT' if video.get('is_short') else ''}\n"
              f"CONTENT:\n<<<{(content or '(none)')[:7000]}>>>\n\n"
              "Model an audience-retention (AVD) curve like YouTube's: x = percent through the video (0-100), "
              "y = percent still watching (starts 100, generally declines with a sharp early hook drop, small "
              "recoveries at strong moments). Then the biggest DROP-OFFS, the most-REPLAYED moments, and blunt "
              "timestamped editor NOTES.\nReturn JSON ONLY: " + _JSON_SHAPE)
    pred = gemini.generate(cfg["gemini_api_key"], prompt, json_mode=True, temperature=0.5, max_tokens=3200)
    return _finalize(pred, video, title, duration_s, has_transcript=bool(content), watched=False)


def predict_video(cfg, path, title=""):
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
        imgs = []
        for _, fp in frames:
            try:
                with open(fp, "rb") as f:
                    imgs.append((f.read(), "image/jpeg"))
            except Exception:
                pass
        tlabels = ", ".join(_mmss(t) for t, _ in frames)
        prompt = (f"{_CRITIC}\nYou are shown {len(imgs)} frames in time order sampled at: {tlabels}.\n"
                  f"TITLE: {title or os.path.basename(path)}\nLENGTH: {_fmt_dur(dur)} ({int(dur)}s)\n"
                  f"AUDIO METRICS: {json.dumps(audio)}\n\n"
                  "Judge the hook, pacing (cuts/min vs the audio metric), dead air (silences), the visuals in the "
                  "frames, and packaging. Model the retention (AVD) curve, the biggest drop-offs, the most-replayed "
                  "moments, and give blunt timestamped editor NOTES tied to what you SEE/measure. Then pitch stronger "
                  "titles and rough thumbnail concepts based on the actual footage.\nReturn JSON ONLY: " + _JSON_SHAPE)
        pred = gemini.generate(cfg["gemini_api_key"], prompt, images=imgs, json_mode=True, temperature=0.55, max_tokens=3600)
        video = {"title": title or os.path.basename(path), "duration_s": dur, "audio": audio}
        return _finalize(pred, video, title or os.path.basename(path), dur, has_transcript=True, watched=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
