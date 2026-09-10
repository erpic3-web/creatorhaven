"""Gemini image generation (Nano Banana) over urllib — powers the thumbnail
generator. Walks image-capable models so a retired one degrades to the next.
generateContent with responseModalities:["IMAGE"] is the surface that works for
these keys (ported from dti-thumbs/thumbforge's proven client, minus `requests`).
"""
import base64
import mimetypes

from . import http

BASE = "https://generativelanguage.googleapis.com/v1beta"
MODELS = ["gemini-3.1-flash-image", "gemini-2.5-flash-image"]


class ImageError(RuntimeError):
    pass


def _part(ref):
    """ref: raw bytes | (bytes, mime) | filesystem path -> an inline_data part."""
    if isinstance(ref, tuple):
        data, mime = ref
    elif isinstance(ref, (bytes, bytearray)):
        data, mime = bytes(ref), "image/png"
    else:
        with open(ref, "rb") as f:
            data = f.read()
        mime = mimetypes.guess_type(str(ref))[0] or "image/png"
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}


def generate_image(api_key, prompt, refs=None, model=None, aspect_ratio="16:9"):
    """Return raw image bytes for `prompt`. `refs` = reference images (face / character /
    style) as bytes/(bytes,mime)/paths. Raises ImageError with a human message."""
    if not api_key:
        raise ImageError("No Gemini API key configured. Add one in Settings → Gemini API key.")
    parts = [{"text": prompt}] + [_part(r) for r in (refs or [])]
    payload = {"contents": [{"parts": parts}],
               "generationConfig": {"responseModalities": ["IMAGE"],
                                    "imageConfig": {"aspectRatio": aspect_ratio}}}
    last = None
    for m in ([model] if model else MODELS):
        try:
            j = http.post_json(f"{BASE}/models/{m}:generateContent", payload,
                               headers={"x-goog-api-key": api_key}, timeout=180, retries=0)
        except http.HttpError as e:
            last = e.message or str(e)
            if e.status in (404, 400) and not model:
                continue                      # model gone for this key -> next
            raise ImageError(f"{e.status}: {last}")
        for cand in j.get("candidates", []):
            for block in cand.get("content", {}).get("parts", []):
                inline = block.get("inlineData") or block.get("inline_data")
                if inline and inline.get("data"):
                    return base64.b64decode(inline["data"])
        fb = j.get("promptFeedback", {})
        if fb.get("blockReason"):
            raise ImageError(f"Blocked by the safety filter ({fb['blockReason']}). Try a milder prompt.")
        last = "the model returned text, not an image"
    raise ImageError(f"No image model available for this key. Last: {last}")
