"""Child process: python -m app.ocr_worker OUT.json img1 img2 ...   (one-shot floorplan OCR, exits afterwards)"""
import sys, json
from . import layout

def main():
    out, paths = sys.argv[1], sys.argv[2:]
    rows = []
    for p in paths:
        try: rows.append({"res": layout.analyse_floorplan(p)})
        except Exception as e: rows.append({"err": f"{type(e).__name__}: {e}"})
    json.dump(rows, open(out, "w"), default=lambda o: o.item() if hasattr(o, "item") else str(o))

main()
