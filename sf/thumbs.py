"""All-around YouTube thumbnail generator (Nano Banana image model), with character
selection. The creator's face, a character/avatar, a brand logo and style references
are saved to disk and attached to each generation so the subject stays consistent
across thumbnails — the thumbforge / dti-thumbs approach, generalized to any niche.
"""
import io
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import config, http, imagegen

REF_KINDS = ("face", "character", "brand", "style")
MAX_REF_BYTES = 8 * 1024 * 1024      # an upload bigger than this is refused
MAX_REFS_PER_KIND = 12               # per account and kind: keeps a public site's disk in check
MAX_REF_SIDE = 2048                  # references are downscaled to this; the image model needs no more


class RefError(ValueError):
    """A reference upload the site refuses (not an image, too big, too many)."""
# Thumbnails, references and generated outputs are private to each user: everything lives
# under data/thumbs/u<uid>/. The current user id travels in a thread-local set per request
# (app.before_request -> thumbs.set_uid), mirroring the store.
_ctx = threading.local()


def set_uid(uid):
    _ctx.uid = int(uid) if uid else 1


def _uid():
    return int(getattr(_ctx, "uid", 1) or 1)


def _base():
    return config.DATA / "thumbs" / f"u{_uid()}"


def refs_root():
    return _base() / "refs"


def out_dir():
    return _base() / "outputs"

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
    d = refs_root() / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clean_image(data):
    """Decode an upload as a real image and re-encode it. That proves it IS an image, drops the
    EXIF/GPS data a stranger's phone photo carries, keeps phone photos upright, and caps the
    size. Returns (bytes, ext)."""
    try:
        from PIL import Image, ImageOps
    except Exception:  # Pillow missing: accept only the magic bytes of the formats we serve
        sig = data[:12]
        if sig.startswith(b"\x89PNG"):
            return data, "png"
        if sig.startswith(b"\xff\xd8\xff"):
            return data, "jpg"
        if sig[:4] == b"RIFF" and sig[8:12] == b"WEBP":
            return data, "webp"
        raise RefError("That file is not a PNG, JPEG or WebP image.")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        im = ImageOps.exif_transpose(im)
    except Exception:
        raise RefError("That file is not an image the generator can use.")
    if max(im.size) > MAX_REF_SIDE:
        im.thumbnail((MAX_REF_SIDE, MAX_REF_SIDE))
    alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    out = io.BytesIO()
    if alpha:
        im.convert("RGBA").save(out, "PNG", optimize=True)
        return out.getvalue(), "png"
    im.convert("RGB").save(out, "JPEG", quality=92)
    return out.getvalue(), "jpg"


def save_ref(kind, data, mime=None):
    """Store one reference image for the signed-in account (private to it)."""
    if not data:
        raise RefError("No image received.")
    if len(data) > MAX_REF_BYTES:
        raise RefError(f"That image is over {MAX_REF_BYTES // (1024 * 1024)} MB. Pick a smaller one.")
    d = _kind_dir(kind)
    if sum(1 for _ in d.glob("*.*")) >= MAX_REFS_PER_KIND:
        raise RefError(f"You already have {MAX_REFS_PER_KIND} {kind} references. Delete one first.")
    clean, ext = _clean_image(data)
    rid = uuid.uuid4().hex[:16]
    (d / f"{rid}.{ext}").write_bytes(clean)
    return {"id": rid, "kind": kind, "url": f"/thumbs/ref/{kind}/{rid}.{ext}", "ext": ext}


def prepare_new_user(uid):
    """A brand-new account starts with an empty thumbnail folder. Account ids are plain integers,
    so a folder left behind by an earlier account (or an old test run) with the same id would
    otherwise hand its face/character images to a stranger. The leftover is moved aside."""
    root = config.DATA / "thumbs"
    old = root / f"u{int(uid)}"
    if not old.exists():
        return None
    dest = root / "_retired" / f"u{int(uid)}-{int(time.time())}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        old.rename(dest)
    except Exception:
        shutil.copytree(old, dest, dirs_exist_ok=True)
        shutil.rmtree(old)
    return dest


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
    od = out_dir(); od.mkdir(parents=True, exist_ok=True)
    ps = sorted(od.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]
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
    vids = [v for v in store.videos(channel_id) if v.get("privacy") == "public" and (v.get("thumb") or v.get("video_id"))]
    added = _import_look(ch.get("thumb"), stats.get("banner"), vids, limit_thumbs)
    return {"added": added, "channel": ch.get("title"), "refs": list_refs()}


def _import_look(avatar_url, banner_url, vids, limit_thumbs):
    """Save a channel's avatar as a character reference and its banner + most-viewed thumbnails
    as style references. A reference the account has no room for is skipped, not an error."""
    added = {"character": 0, "style": 0}

    def keep(kind, data):
        if not data:
            return
        try:
            save_ref(kind, data, "image/jpeg")
            added[kind] += 1
        except RefError:
            pass

    keep("character", _download(avatar_url))
    keep("style", _download(banner_url))
    vids = sorted(vids, key=lambda v: v.get("views") or 0, reverse=True)
    for v in vids[:limit_thumbs]:
        vid = v.get("video_id")
        data = _download(f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg") if vid else None
        keep("style", data or _download(v.get("thumb")))
    return added


def import_from_public(yt, ref, limit_thumbs=5):
    """Import any channel's look by @handle, channel URL or UC id through the public Data API
    (about 3 quota units), so an account with no tracked channels can still bring its brand."""
    from . import search
    hit = search._direct_ref((ref or "").strip())
    if not hit:
        raise RefError("Paste your channel's @handle or its youtube.com link.")
    kind, val = hit
    found = yt.channels_by_ids([val]) if kind == "id" else [yt.channel_by_handle(val)]
    ch = next((c for c in found if c), None)
    if not ch:
        raise RefError("Couldn't find that channel.")
    vids = []
    if ch.get("uploads_playlist"):
        ids = yt.playlist_video_ids(ch["uploads_playlist"], limit=50)
        if ids:
            vids = [v for v in yt.videos(ids) if v.get("privacy") in (None, "public")]
    added = _import_look(ch.get("thumb"), ch.get("banner"), vids, limit_thumbs)
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
    od = out_dir(); od.mkdir(parents=True, exist_ok=True)
    oid = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    (od / f"{oid}.png").write_bytes(img)
    return {"id": oid, "url": f"/thumbs/out/{oid}.png", "prompt": prompt, "refs_used": len(ref_files)}


def migrate_legacy():
    """One-time: move the pre-multi-user thumbnail store (data/thumbs/refs + outputs) under the
    owner account (data/thumbs/u1/). Safe to call on every start — a no-op once migrated."""
    root = config.DATA / "thumbs"
    u1 = root / "u1"
    for name in ("refs", "outputs"):
        src = root / name
        dst = u1 / name
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                src.rename(dst)
            except Exception:
                pass
