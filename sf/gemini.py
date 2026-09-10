"""Gemini text + vision over urllib for the LLM-backed tools (tag generator,
ad-safety check, thumbnail analyzer). Walks a model list so a retired model
degrades to the next one instead of breaking the tool.
"""
import base64
import json
import mimetypes

from . import http

BASE = "https://generativelanguage.googleapis.com/v1beta"
MODELS = ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-2.5-flash"]


class GeminiError(RuntimeError):
    pass


def _parts(prompt, images):
    parts = [{"text": prompt}]
    for img in images or []:
        if isinstance(img, tuple):
            data, mime = img
        else:
            data = open(img, "rb").read()
            mime = mimetypes.guess_type(str(img))[0] or "image/png"
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    return parts


def _parse_json(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    try:
        return json.loads(t, strict=False)
    except Exception:
        pass
    s, e = t.find("{"), t.rfind("}")
    if s >= 0 and e > s:
        return json.loads(t[s:e + 1], strict=False)
    s, e = t.find("["), t.rfind("]")
    if s >= 0 and e > s:
        return json.loads(t[s:e + 1], strict=False)
    raise ValueError("no JSON object found")


def generate(api_key, prompt, images=None, json_mode=False, model=None, temperature=0.4, base=None,
             max_tokens=16384, json_retries=2, continuations=2):
    base = base or BASE
    if not api_key:
        raise GeminiError("No Gemini API key configured")
    payload = {"contents": [{"role": "user", "parts": _parts(prompt, images)}],
               "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
    last = None
    for m in ([model] if model else MODELS):
        try:
            j = http.post_json(f"{base}/models/{m}:generateContent", payload,
                               headers={"x-goog-api-key": api_key}, timeout=120)
        except http.HttpError as e:
            last = e
            if e.status in (404, 400) and not model:
                continue      # model gone for this key -> next
            raise GeminiError(str(e))
        try:
            cand = j["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"])
        except Exception:
            raise GeminiError(f"Unexpected Gemini response: {json.dumps(j)[:300]}")
        # Gemini 3.x thinking models count their reasoning against maxOutputTokens, so a long
        # answer can stop mid-sentence with finishReason MAX_TOKENS. Ask it to carry on from
        # where it stopped (up to `continuations` times) and stitch the halves together.
        if cand.get("finishReason") == "MAX_TOKENS" and continuations > 0 and text.strip():
            tail = text[-600:]
            more = generate(api_key, prompt + "\n\n=== YOUR ANSWER SO FAR (cut off by a length limit) ===\n"
                            + text + "\n\nContinue EXACTLY from where it stopped - do not repeat anything above, "
                            "do not restart, just finish the answer" + (" as valid JSON continuing the same document"
                                                                          if json_mode else "") + ".",
                            images=images, json_mode=False, model=m, temperature=temperature, base=base,
                            max_tokens=max_tokens, json_retries=0, continuations=continuations - 1)
            text = text + more
        if json_mode:
            try:
                return _parse_json(text)
            except Exception as e:
                # long structured answers (idea boards) sometimes come back with an unescaped
                # quote or a truncated tail — ask again rather than fail the whole request
                if json_retries > 0:
                    return generate(api_key, prompt + "\n\n(Your previous answer was not valid JSON: "
                                    f"{str(e)[:120]}. Return strictly valid JSON only — escape any double quotes "
                                    "inside strings, no trailing commas, no markdown fences.)",
                                    images=images, json_mode=True, model=m, temperature=max(0.2, temperature - 0.2),
                                    base=base, max_tokens=max_tokens, json_retries=json_retries - 1)
                raise GeminiError("Gemini returned non-JSON: " + text[:200])
        return text
    raise GeminiError(f"No Gemini text model available for this key. Last: {last}")
