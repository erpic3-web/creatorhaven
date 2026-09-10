"""All-around YouTube thumbnail generator (Nano Banana image model), with character
selection. The creator's face, a character/avatar, a brand logo and style references
are saved to disk and attached to each generation so the subject stays consistent
across thumbnails — the thumbforge / dti-thumbs approach, generalized to any niche.
"""
import json
import time
import uuid
from pathlib import Path

from . import config, http, imagegen

REF_KINDS = ("face", "character", "brand", "style")
REFS_DIR = config.DATA / "thumbs" / "refs"
OUT_DIR = config.DATA / "thumbs" / "outputs"
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp"}

# General-purpose style presets (any niche). Each carries a prompt fragment.
STYLES = {
    "mrbeast":   {"name": "MrBeast / hype", "desc": "hyper-saturated, shocked face, big spectacle",
                  "prompt": "MrBeast-style hype thumbnail: hyper-saturated punchy colors, an exaggerated shocked/excited human expression as the primary read, big spectacle or money/stakes prop, extreme contrast, subject cut out with a crisp edge and glow."},
    "clean":     {"name": "Clean / minimal", "desc": "one subject, negative space, premium",
                  "prompt": "Clean minimal thumbnail: a single clear subject, generous negative space, one bold word, softly-lit muted background, premium modern look."},
    "gaming":    {"name": "Gaming", "desc": "game render, glow, action",
                  "prompt": "Gaming thumbnail: dynamic game character/render, energetic glow and rim light, action framing, one punchy accent color, high contrast."},
    "reaction":  {"name": "Reaction / facecam", "desc": "big face + the thing reacted to",
                  "prompt": "Reaction thumbnail: a large expressive face on one third of the frame reacting, the subject being reacted to on the other side, a bold circle or arrow callout drawing the eye."},
    "cinematic": {"name": "Cinematic", "desc": "filmic grade, moody, poster",
                  "prompt": "Cinematic thumbnail: dramatic directional lighting, filmic color grade, depth and atmosphere, movie-poster composition, moody but readable."},
    "vlog":      {"name": "Lifestyle / vlog", "desc": "warm, candid, real place",
                  "prompt": "Lifestyle vlog thumbnail: warm natural light, candid authentic subject, a real recognizable location behind, friendly inviting mood, a hand-written style accent."},
    "explainer": {"name": "Explainer / finance", "desc": "chart motif, bold number, trustworthy",
                  "prompt": "Explainer thumbnail: a clean chart/diagram or icon motif, one bold number or keyword, trustworthy blue/green palette with a single warm accent, uncluttered."},
    "horror":    {"name": "Horror / dramatic", "desc": "dark, tense, red accent",
                  "prompt": "Horror/dramatic thumbnail: dark high-contrast scene, an unsettling subject, deep shadows, a single red accent, tense atmosphere, still clearly readable at small size."},
    "versus":    {"name": "Versus / compare", "desc": "split, two subjects, VS",
                  "prompt": "Versus thumbnail: a split composition with two subjects facing off, a bold VS in the middle, contrasting color on each side."},
}

COMPOSITION = ("Composition & color rules: 16:9, must read in under a second at 168px feed size. "
               "Exactly ONE primary read, one secondary (the twist), text last. Rule of thirds — put "
               "the face/eyes on a third line, not dead center. Warm saturated hues advance (red, "
               "orange, yellow, pink); use complementary contrast so the subject pops; 60-30-10 color "
               "split; separate the subject from the background with rim light / outline / shadow. Keep "
               "the bottom-right corner clear (duration stamp) and leave breathing room around text. "
               "Photoreal unless a style says otherwise. No watermark, no YouTube UI, no logos unless asked.")


def _kind_dir(kind):
    if kind not in REF_KINDS:
        raise ValueError(f"unknown reference kind: {kind}")
    d = REFS_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_ref(kind, data, mime):
    ext = _EXT.get((mime or "").lower(), "png")
    rid = uuid.uuid4().hex[:16]
    (_kind_dir(kind) / f"{rid}.{ext}").write_bytes(data)
    return {"id": rid, "kind": kind, "url": f"/thumbs/ref/{kind}/{rid}.{ext}", "ext": ext}


def _find(kind, rid):
    d = _kind_dir(kind)
    for p in d.glob(f"{rid}.*"):
        return p
    return None


def ref_path(kind, rid):
    return _find(kind, rid)


def delete_ref(kind, rid):
    p = _find(kind, rid)
    if p:
        p.unlink(missing_ok=True)
        return True
    return False


def list_refs():
    out = {}
    for kind in REF_KINDS:
        d = _kind_dir(kind)
        items = []
        for p in sorted(d.glob("*.*"), key=lambda x: x.stat().st_mtime, reverse=True):
            rid = p.stem
            items.append({"id": rid, "url": f"/thumbs/ref/{kind}/{p.name}"})
        out[kind] = items
    return out


def list_outputs(limit=40):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ps = sorted(OUT_DIR.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]
    return [{"id": p.stem, "url": f"/thumbs/out/{p.name}"} for p in ps]


_LEGEND = {
    "face": "the PERSON to feature — keep their face, hair and identity exactly recognizable",
    "character": "the CHARACTER/avatar to feature — reproduce it faithfully",
    "brand": "a logo/brand mark to include cleanly and subtly",
    "style": "a STYLE reference — match its look and mood, NOT its literal content",
}


def build_prompt(style, subject, text, extra, ref_meta):
    sty = STYLES.get(style) or {"prompt": ""}
    lines = ["Create a professional, click-worthy YouTube thumbnail (16:9)."]
    if subject:
        lines.append(f"Subject / scene: {subject}.")
    if sty["prompt"]:
        lines.append(sty["prompt"])
    if text:
        lines.append(f'Render this exact text on the thumbnail, spelled perfectly, big and readable with a '
                     f'heavy outline, keywords in a warm accent color: "{text}". No other text.')
    else:
        lines.append("No text on the thumbnail unless it is part of the scene.")
    if extra:
        lines.append(extra)
    if ref_meta:
        lines.append("Reference images provided, in order:")
        for i, (kind, _) in enumerate(ref_meta, 1):
            lines.append(f"  Image {i}: {_LEGEND.get(kind, 'a reference')}.")
    lines.append(COMPOSITION)
    return "\n".join(lines)


def _download(url):
    if not url:
        return None
    try:
        b, _ = http.request(url, timeout=20, retries=1)
        return b if (b and len(b) > 1500) else None
    except Exception:
        return None


def import_from_channel(store, channel_id, limit_thumbs=5):
    """Preload the operator's OWN look: the channel avatar as a character/face reference and
    the top recent thumbnails as style references, so generations match their brand."""
    ch = store.channel(channel_id)
    if not ch:
        raise ValueError("channel not found")
    stats = {}
    try:
        stats = json.loads(ch.get("stats_json") or "{}")
    except Exception:
        pass
    added = {"character": 0, "style": 0}
    av = _download(ch.get("thumb"))
    if av:
        save_ref("character", av, "image/jpeg"); added["character"] += 1
    banner = _download(stats.get("banner"))
    if banner:
        save_ref("style", banner, "image/jpeg"); added["style"] += 1
    vids = [v for v in store.videos(channel_id) if v.get("privacy") == "public" and (v.get("thumb") or v.get("video_id"))]
    vids.sort(key=lambda v: v.get("views") or 0, reverse=True)
    for v in vids[:limit_thumbs]:
        b = _download(v.get("thumb") or f"https://i.ytimg.com/vi/{v['video_id']}/hqdefault.jpg")
        if b:
            save_ref("style", b, "image/jpeg"); added["style"] += 1
    return {"added": added, "channel": ch.get("title"), "refs": list_refs()}


def generate(cfg, style="mrbeast", subject="", text="", extra="", refs=None, aspect="16:9"):
    refs = refs or []
    ref_files, ref_meta = [], []
    for r in refs:
        kind, rid = r.get("kind"), r.get("id")
        p = _find(kind, rid) if kind and rid else None
        if p and p.exists():
            mime = "image/png" if p.suffix == ".png" else ("image/webp" if p.suffix == ".webp" else "image/jpeg")
            ref_files.append((p.read_bytes(), mime))
            ref_meta.append((kind, rid))
    prompt = build_prompt(style, subject, text, extra, ref_meta)
    img = imagegen.generate_image(cfg["gemini_api_key"], prompt, refs=ref_files, aspect_ratio=aspect)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    oid = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    (OUT_DIR / f"{oid}.png").write_bytes(img)
    return {"id": oid, "url": f"/thumbs/out/{oid}.png", "prompt": prompt, "refs_used": len(ref_files)}
