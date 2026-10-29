"""Pluggable vision-LLM analyser.  OFF unless an API key is configured (env PROPDASH_VISION_API_KEY or
settings).  Providers: 'openai' (any OpenAI-compatible /chat/completions endpoint: OpenAI, Azure-compat,
OpenRouter, Ollama, LM Studio, vLLM ...) and 'anthropic' (Messages API).  Returns structured JSON."""
import base64, json, mimetypes, os, re
import httpx

QUESTIONNAIRE = """You are assessing a UK house for sale from its floor plan(s) and photos.
Answer ONLY with minified JSON using exactly these keys. For each yes/no question use one of "yes","no","unknown"
and add a short evidence string and a confidence 0-1.
{"kitchen_diner":{"a":"","evidence":"","conf":0},
 "family_space_adjoining_kitchen":{"a":"","evidence":"","conf":0},
 "downstairs_wc":{"a":"","evidence":"","conf":0},
 "ensuite":{"a":"","evidence":"","conf":0},
 "utility_room":{"a":"","evidence":"","conf":0},
 "separate_reception_rooms":{"n":0,"evidence":"","conf":0},
 "study_or_office":{"a":"","evidence":"","conf":0},
 "driveway_off_street_parking":{"a":"","evidence":"","conf":0},
 "garden_large":{"a":"","evidence":"","conf":0},
 "not_overlooked":{"a":"","evidence":"","conf":0},
 "bedrooms":{"n":0,"conf":0},
 "approx_total_sqft":{"n":0,"conf":0},
 "overall_layout_notes":""}"""

def key(cfg):
    return os.environ.get(cfg.get("api_key_env") or "PROPDASH_VISION_API_KEY") or cfg.get("api_key") or ""

def available(cfg):
    return bool(cfg.get("enabled") and key(cfg))

def _b64(path):
    mt = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    from PIL import Image
    import io
    with Image.open(path) as im:
        im = im.convert("RGB"); im.thumbnail((1400, 1400))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return "image/jpeg", base64.b64encode(buf.getvalue()).decode()

def analyse(cfg, image_paths, context_text=""):
    k = key(cfg)
    if not (cfg.get("enabled") and k):
        return {"available": False, "reason": "vision analyser disabled or no API key"}
    imgs = [_b64(p) for p in image_paths[: int(cfg.get("max_images", 6))]]
    prompt = QUESTIONNAIRE + ("\nListing text for context:\n" + context_text[:3000] if context_text else "")
    try:
        if cfg.get("provider") == "anthropic":
            content = [{"type": "image", "source": {"type": "base64", "media_type": m, "data": d}} for m, d in imgs]
            content.append({"type": "text", "text": prompt})
            r = httpx.post(cfg.get("base_url", "https://api.anthropic.com") .rstrip("/") + "/v1/messages",
                           headers={"x-api-key": k, "anthropic-version": "2023-06-01"}, timeout=120,
                           json={"model": cfg["model"], "max_tokens": 1500, "messages": [{"role": "user", "content": content}]})
            r.raise_for_status(); txt = r.json()["content"][0]["text"]
        else:
            content = [{"type": "text", "text": prompt}] + [{"type": "image_url", "image_url": {"url": f"data:{m};base64,{d}"}} for m, d in imgs]
            r = httpx.post(cfg.get("base_url", "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
                           headers={"Authorization": f"Bearer {k}"}, timeout=120,
                           json={"model": cfg["model"], "messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": 1500})
            r.raise_for_status(); txt = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0))
        return {"available": True, "model": cfg["model"], "provider": cfg.get("provider"), "answers": data, "n_images": len(imgs)}
    except Exception as e:
        return {"available": True, "error": f"{type(e).__name__}: {e}"}

# mapping vision answers -> checklist keys
def to_checklist(ans):
    out = {}
    def yn(k): 
        v = (ans.get(k) or {})
        return v.get("a"), float(v.get("conf") or 0), v.get("evidence")
    def put(key, a, conf, ev):
        if conf < 0.5 or a not in ("yes", "no"): return
        out[key] = {"state": "confirmed" if a == "yes" else "contradicted", "evidence": f"[vision {conf:.0%}] {ev or ''}"}
    a, c, e = yn("family_space_adjoining_kitchen"); put("kitchen_family", a, c, e)
    a, c, e = yn("utility_room"); put("utility", a, c, e)
    a, c, e = yn("driveway_off_street_parking"); put("parking", a, c, e)
    a, c, e = yn("garden_large"); put("garden_size", a, c, e)
    a, c, e = yn("not_overlooked"); put("not_overlooked", a, c, e)
    a1, c1, e1 = yn("downstairs_wc"); a2, c2, e2 = yn("ensuite")
    if a1 == "yes" and c1 >= .5: put("wc_or_ensuite", "yes", c1, e1)
    elif a2 == "yes" and c2 >= .5: put("wc_or_ensuite", "yes", c2, e2)
    elif a1 == "no" and a2 == "no": put("wc_or_ensuite", "no", min(c1, c2), (e1 or "") + " " + (e2 or ""))
    return out
