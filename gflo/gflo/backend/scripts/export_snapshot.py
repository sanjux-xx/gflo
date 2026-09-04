#!/usr/bin/env python3
"""Write a standalone copy of the storefront that needs no backend.

Bakes the current catalogue into the HTML (snapshot mode) and inlines every
locally-stored product photo, so the file browses fully offline — useful for
sharing a preview, or for hosting the shop on a static host / CDN while the
admin panel runs elsewhere.

    python scripts/export_snapshot.py                 # -> ../site/gflo-snapshot.html
    python scripts/export_snapshot.py out.html        # custom path
    python scripts/export_snapshot.py out.html --full # full-size photos (much bigger file)

Note: a snapshot is frozen. Re-run it after catalogue edits.
"""
import base64, io, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal, MEDIA_DIR                     # noqa: E402
from app.models import Product, Category, Brand                # noqa: E402
from app.api import product_json                               # noqa: E402
from app.store import get_setting, PUBLIC_SETTING_KEYS         # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_DIR = os.environ.get("SITE_DIR", os.path.join(os.path.dirname(BACKEND), "site"))
MARKER = '<script>\n"use strict";\n/* ================= LIVE CATALOGUE'

_cache = {}


def data_uri(url, box):
    if url in _cache:
        return _cache[url]
    path = os.path.join(MEDIA_DIR, "products", os.path.basename(url))
    if not os.path.exists(path):
        _cache[url] = ""
        return ""
    raw = open(path, "rb").read()
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    if box:
        try:
            from PIL import Image
            buf = io.BytesIO()
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((box, box))
                im.save(buf, "JPEG", quality=72)
            raw, mime = buf.getvalue(), "image/jpeg"
        except Exception:
            pass
    _cache[url] = f"data:{mime};base64," + base64.b64encode(raw).decode()
    return _cache[url]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = args[0] if args else os.path.join(SITE_DIR, "gflo-snapshot.html")
    box = None if "--full" in sys.argv else 300

    src = os.path.join(SITE_DIR, "gflo.html")
    if not os.path.exists(src):
        sys.exit(f"can't find {src} — set SITE_DIR")
    html = open(src, encoding="utf-8").read()
    if MARKER not in html:
        sys.exit("gflo.html doesn't look like a v33 build (catalogue loader not found)")

    db = SessionLocal()
    try:
        show_prices = get_setting(db, "show_prices", "true") == "true"
        catalog = {
            "version": 1,
            "settings": {k: get_setting(db, k, "") for k in PUBLIC_SETTING_KEYS},
            "showPrices": show_prices,
            "categories": [{"id": c.id, "name": c.name, "code": c.code, "hue": c.hue,
                            "desc": c.description, "popular": bool(c.popular),
                            "icon": c.icon or "", "img": c.image_url or ""}
                           for c in db.query(Category).order_by(Category.sort_order, Category.name)],
            "brands": [{"id": b.id, "name": b.name, "hue": b.hue}
                       for b in db.query(Brand).order_by(Brand.sort_order, Brand.name)],
            "products": [product_json(p, show_prices) for p in
                         db.query(Product).filter(Product.visible == True)      # noqa: E712
                         .order_by(Product.sort_order, Product.id)],
        }
    finally:
        db.close()

    inlined = 0
    for p in catalog["products"]:
        main_img = p.get("img", "")
        if main_img.startswith("/media/"):
            uri = data_uri(main_img, box)
            if uri:
                p["img"] = uri
                inlined += 1
        # skip gallery copies of the main photo so the same bytes aren't embedded twice
        extra = [u for u in p.get("imgs", []) if u != main_img]
        p["imgs"] = [data_uri(u, box) if u.startswith("/media/") else u for u in extra]
        p["imgs"] = [u for u in p["imgs"] if u]
    for c in catalog["categories"]:
        if c.get("img", "").startswith("/media/"):
            c["img"] = data_uri(c["img"], box or 400)

    payload = json.dumps(catalog, separators=(",", ":"))
    html = html.replace(MARKER, f"<script>window.GFLO_PRELOAD = {payload};</script>\n" + MARKER, 1)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    size = os.path.getsize(out) / 1024 / 1024
    print(f"wrote {out}  ({size:.2f} MB · {len(catalog['products'])} products · {inlined} photos inlined)")
    print("open it straight from disk — no server needed")


if __name__ == "__main__":
    main()
