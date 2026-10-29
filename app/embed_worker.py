"""Child process: python -m app.embed_worker OUT.npy img1 img2 ...   (one-shot CLIP embedding, exits afterwards)"""
import sys
import numpy as np
from . import embed

def main():
    out, paths = sys.argv[1], sys.argv[2:]
    if not embed._load():
        sys.stderr.write("clip model unavailable: %s" % embed.status.get("error")); sys.exit(3)
    rows = []
    for p in paths:
        v = embed.embed_image(p)
        rows.append(v if v is not None else np.full(512, np.nan, dtype=np.float32))
    np.save(out, np.stack(rows).astype(np.float32))

main()
