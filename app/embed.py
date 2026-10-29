"""Local image embeddings: CLIP ViT-B/32 vision tower (ONNX, quantised, ~89MB, downloaded once from
Hugging Face 'Xenova/clip-vit-base-patch32').  Falls back to a colour-histogram vector if unavailable."""
import os, sys, subprocess, tempfile, threading
import numpy as np
from .config import DATA

MODEL = DATA / "models" / "clip_vision_q.onnx"
URL = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/vision_model_quantized.onnx"
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
_sess = None
_lock = threading.Lock()
status = {"backend": "unloaded", "error": None}
WORKER = os.environ.get("PROPDASH_EMBED_WORKER") == "1"
SUBPROC = os.environ.get("PROPDASH_LEAN") == "1" and not WORKER   # low-memory hosting: CLIP runs in a short-lived child process

def _load():
    global _sess
    if _sess is not None: return _sess
    try:
        import onnxruntime as ort
        if not MODEL.exists() or MODEL.stat().st_size < 50_000_000:
            import httpx
            tmp = MODEL.with_suffix(".part")
            with httpx.stream("GET", URL, follow_redirects=True, timeout=300) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20): f.write(chunk)
            tmp.rename(MODEL)
        so = ort.SessionOptions()
        if WORKER or os.environ.get("PROPDASH_LEAN") == "1":   # smallest RAM footprint
            so.enable_cpu_mem_arena = False; so.enable_mem_pattern = False; so.intra_op_num_threads = 1
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        _sess = ort.InferenceSession(str(MODEL), so, providers=["CPUExecutionProvider"])
        status.update(backend="clip-vit-b32-onnx", error=None)
    except Exception as e:
        _sess = False
        status.update(backend="colour-histogram-fallback", error=str(e))
    return _sess

def _prep(img):
    from PIL import Image
    img = img.convert("RGB")
    w, h = img.size; s = 224 / min(w, h)
    img = img.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
    w, h = img.size; l, t = (w - 224) // 2, (h - 224) // 2
    a = np.asarray(img.crop((l, t, l + 224, t + 224)), dtype=np.float32) / 255.0
    return ((a - MEAN) / STD).transpose(2, 0, 1)[None]

def embed_many(paths):
    """-> list of L2-normalised vectors (or None per failed image).  In lean mode runs one child process per batch
    (model loaded, batch embedded, process exits -> RAM is returned to the OS)."""
    paths = [str(p) for p in paths]
    if not paths: return []
    if not SUBPROC: return [embed_image(p) for p in paths]
    out = [None] * len(paths)
    with _lock:
        fd, tmp = tempfile.mkstemp(suffix=".npy"); os.close(fd)
        try:
            r = subprocess.run([sys.executable, "-m", "app.embed_worker", tmp] + paths, capture_output=True, timeout=900,
                               env=dict(os.environ, PROPDASH_EMBED_WORKER="1"), cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent))
            if r.returncode != 0:
                status.update(backend="clip-subprocess-failed", error=(r.stderr or b"")[-300:].decode("utf8", "replace")); return out
            arr = np.load(tmp, allow_pickle=False)     # shape (n, 512); rows of NaN = failed image
            for i in range(min(len(out), len(arr))):
                if not np.isnan(arr[i]).any(): out[i] = arr[i].astype(np.float32)
            status.update(backend="clip-vit-b32-onnx (subprocess)", error=None)
        except Exception as e:
            status.update(backend="clip-subprocess-failed", error=f"{type(e).__name__}: {e}")
        finally:
            try: os.unlink(tmp)
            except Exception: pass
    return out

def embed_image(path):
    """-> np.float32 vector (L2-normalised) or None"""
    if SUBPROC: return embed_many([path])[0]
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.load(); img = im.copy()
    except Exception:
        return None
    with _lock:
        s = _load()
        if s:
            v = s.run(None, {"pixel_values": _prep(img)})[0][0].astype(np.float32)
        else:
            a = np.asarray(img.convert("RGB").resize((64, 64)), dtype=np.float32) / 255
            hist = np.concatenate([np.histogram(a[..., c], bins=16, range=(0, 1))[0] for c in range(3)]).astype(np.float32)
            v = hist
    n = np.linalg.norm(v)
    return v / n if n else v

def cos(a, b):
    if a is None or b is None or a.shape != b.shape: return 0.0
    return float(np.dot(a, b))

def to_blob(v): return v.astype(np.float32).tobytes()
def from_blob(b): return np.frombuffer(b, dtype=np.float32) if b else None
