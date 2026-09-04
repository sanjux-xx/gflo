#!/usr/bin/env python3
"""Seed the database: categories, brands, the existing 700-product catalogue,
then the G-FLO 2026 price list.

Safe to re-run — it matches on SKU and updates rather than duplicating.
Products you created by hand in the admin panel are never touched.

    python scripts/seed.py                 # everything
    python scripts/seed.py --skip-legacy   # only categories/brands + price list
"""
import json, os, shutil, sys, hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, engine, SessionLocal, MEDIA_DIR, ensure_schema     # noqa: E402
from app.models import Product, Category, Brand                            # noqa: E402
from app.store import ensure_defaults                                      # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SEED_IMAGES = os.path.join(DATA, "seed_images")

# extra categories introduced by the printed price list
NEW_CATEGORIES = [
    ("fan-pipes", "Fan Pipes & Rods", "FP", 8, "Ultra heavy and heavy down rods in seven colours", True, 10),
    ("clamps-hooks", "Clamps, Hooks & Fasteners", "CH", 32, "Fan clamps, jhula hooks, anchor fasteners", True, 20),
    ("street-light", "Street Light Clamps", "SL", 200, "Stands, wall clamps and pole clamps", True, 30),
    ("power-tools", "Power & Hand Tools", "PT", 218, "Drills, grinders, screwdrivers, pliers, testers", True, 40),
    ("blades", "Cutting Blades", "BL", 24, "Stone, steel and wood cutting blades", True, 50),
    ("fasteners", "Screws & Fasteners", "FS", 45, "Drywall, self-drilling and machine screws, wall plugs", True, 60),
    ("electrical", "Electrical Accessories", "EL", 350, "Capacitors, stators, tapes, connectors, immersion rods", True, 70),
    ("plumbing", "Plumbing Accessories", "PL", 190, "Connection pipes, hoses, clamps, tapes, flexible pipe", True, 80),
    ("cable-management", "Cable Management", "CB", 262, "Cable ties, nail clips, battens, menscore wire", False, 90),
    ("measuring", "Measuring Tools", "MT", 96, "Measuring tapes and auto tapes", False, 100),
]

PT_BY_GROUP = {
    "Ultra Heavy Fan Pipes": "body", "Heavy Fan Pipes": "body",
    "Clamps, Hooks & Anchor Fasteners": "screw-kit", "Street Light Clamps & Accessories": "body",
    "Tools & Accessories": "spares", "Electrical Accessories": "spares",
    "Plumbing Accessories": "spares", "Other Accessories": "spares",
    "Screws": "screw-kit", "Cable Management": "clip-kit", "Flexible Pipe": "body",
    "PVC Wall Plug": "screw-kit", "Menscore": "spares",
}
PT_BY_CAT = {"blades": "blade", "measuring": "spares", "power-tools": "spares"}


def copy_seed_image(filename: str, prefix: str = "") -> str:
    """Copy a bundled photo into the media folder and return its public URL."""
    if not filename:
        return ""
    src = os.path.join(SEED_IMAGES, filename)
    if not os.path.exists(src):
        return ""
    dest_name = (prefix + filename).replace(" ", "-")
    dest = os.path.join(MEDIA_DIR, "products", dest_name)
    if not os.path.exists(dest):
        shutil.copy2(src, dest)
    return f"/media/products/{dest_name}"


def upsert(db, sku, defaults, protect_manual=True):
    p = db.query(Product).filter(Product.sku == sku).first()
    if p is None:
        p = Product(sku=sku, **defaults)
        db.add(p)
        db.flush()
        attach_image(db, p)
        return p, True
    if protect_manual and p.source == "manual":
        return p, False
    for key, value in defaults.items():
        setattr(p, key, value)
    attach_image(db, p)
    return p, False


def attach_image(db, p):
    """Make the seeded photo a managed gallery image so it can be replaced,
    re-ordered or removed from the admin panel like an uploaded one."""
    from app.models import ProductImage
    url = p.image_url or ""
    if not url.startswith("/media/"):
        return
    exists = (db.query(ProductImage)
              .filter(ProductImage.product_id == p.id, ProductImage.url == url).first())
    if not exists:
        db.add(ProductImage(product_id=p.id, url=url, sort_order=0))


def seed_taxonomy(db):
    legacy = json.load(open(os.path.join(DATA, "legacy_catalog.json"), encoding="utf-8"))
    for c in legacy["categories"]:
        row = db.get(Category, c["id"]) or Category(id=c["id"])
        row.name, row.code, row.hue = c["name"], c["code"], c["hue"]
        row.description, row.popular = c["description"], c["popular"]
        row.icon, row.sort_order = c.get("icon", ""), c["sort_order"]
        db.add(row)
    for cid, name, code, hue, desc, popular, order in NEW_CATEGORIES:
        row = db.get(Category, cid) or Category(id=cid)
        row.name, row.code, row.hue = name, code, hue
        row.description, row.popular, row.sort_order = desc, popular, order
        db.add(row)
    for b in legacy["brands"]:
        row = db.get(Brand, b["id"]) or Brand(id=b["id"])
        row.name, row.hue, row.sort_order = b["name"], b["hue"], b["sort_order"]
        db.add(row)
    gflo = db.get(Brand, "gflo") or Brand(id="gflo")
    gflo.name, gflo.hue, gflo.sort_order = "G-FLO", 358, 1
    db.add(gflo)
    db.commit()
    print(f"categories: {db.query(Category).count()}  brands: {db.query(Brand).count()}")


def seed_legacy_products(db):
    legacy = json.load(open(os.path.join(DATA, "legacy_catalog.json"), encoding="utf-8"))
    cat_codes = {c.id: c.code for c in db.query(Category).all()}
    created = updated = 0
    serial = 100
    for row in legacy["products"]:
        serial += 1 + (int(hashlib.md5(row["name"].encode()).hexdigest(), 16) % 4)
        code = cat_codes.get(row["cat"], "GN")
        pt3 = "".join(ch for ch in row["pt"] if ch.isalpha())[:3].upper()
        sku = f"GF-{code}-{pt3}-{serial:04d}"
        defaults = dict(
            name=row["name"], category_id=row["cat"], part_family=row["pt"],
            group_name="Appliance Spares", price=row["price"], mrp=row["mrp"],
            stock=25 if row["stock"] else 0, unit="piece",
            rating=row["rating"] or 4.2, reviews=row["reviews"] or 0,
            image_url=row["image"], visible=True, source="legacy",
            sort_order=1000 + row["seq"],
        )
        _, is_new = upsert(db, sku, defaults)
        created += is_new
        updated += 0 if is_new else 1
    for i, t in enumerate(legacy["tools"]):
        sku = f"GF-TL-{t['photo_key'][:10].upper().replace('-', '')}-{i + 1:03d}"
        defaults = dict(
            name=t["name"], category_id="tools", part_family=t["pt"],
            group_name="Tools & Hardware", price=t["price"], mrp=t["mrp"],
            stock=25, unit="piece", rating=t["rating"], reviews=t["reviews"],
            brand_names="G-FLO", visible=True, source="legacy", sort_order=500 + i,
            image_url=copy_seed_image(t["photo_key"] + ".jpg", "tool-"),
        )
        _, is_new = upsert(db, sku, defaults)
        created += is_new
    db.commit()
    print(f"legacy products — created {created}, refreshed {updated}")


def seed_pricelist(db, prices_only=False):
    rows = json.load(open(os.path.join(DATA, "pl_products.json"), encoding="utf-8"))
    created = updated = 0
    for i, r in enumerate(rows):
        sku = r["code"]
        existing = db.query(Product).filter(Product.sku == sku).first()
        if prices_only:
            if existing:
                existing.price = r["price"]
                updated += 1
            continue
        pt = PT_BY_CAT.get(r["cat"]) or PT_BY_GROUP.get(r["group"], "spares")
        bits = [r["note"]]
        if r["size"]:
            bits.append(f"Size: {r['size']}.")
        if r["pack"]:
            bits.append(f"Packing: {r['pack']}.")
        if r["colours"]:
            bits.append(f"Available colours: {r['colours']}.")
        if r["material"]:
            bits.append(f"Material: {r['material']}.")
        bits.append(f"G-FLO code {r['code'].split('-FP')[0].split('-WP')[0]}.")
        defaults = dict(
            name=r["name"], category_id=r["cat"], part_family=pt,
            group_name=r["group"], price=r["price"], mrp=None,
            stock=25, unit=r["unit"], size=r["size"], pack=r["pack"],
            colours=r["colours"], material=r["material"],
            description=" ".join(b for b in bits if b),
            brand_names="G-FLO", warranty="", weight="",
            rating=0, reviews=0, visible=True, source="pricelist",
            sort_order=100 + i,
            image_url=copy_seed_image(r["image"], "pl-"),
        )
        _, is_new = upsert(db, sku, defaults)
        created += is_new
        updated += 0 if is_new else 1
    db.commit()
    print(f"price list — created {created}, refreshed {updated}")


def main():
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    db = SessionLocal()
    try:
        ensure_defaults(db)
        seed_taxonomy(db)
        if "--skip-legacy" not in sys.argv:
            seed_legacy_products(db)
        seed_pricelist(db, prices_only="--prices" in sys.argv)
        total = db.query(Product).count()
        priced = db.query(Product).filter(Product.price != None).count()   # noqa: E711
        print(f"\ndone — {total} products in the catalogue, {priced} with a price, "
              f"{total - priced} on request")
        print("start the server with:  ./run.sh     then open http://localhost:8000/admin")
    finally:
        db.close()


if __name__ == "__main__":
    main()
