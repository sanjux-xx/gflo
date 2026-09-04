"""Public read-only JSON API consumed by the storefront."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .db import get_db
from .models import Product, Category, Brand
from .store import get_setting, PUBLIC_SETTING_KEYS

router = APIRouter(prefix="/api", tags=["public"])


def product_json(p: Product, show_prices: bool) -> dict:
    return {
        "sku": p.sku,
        "name": p.name,
        "cat": p.category_id,
        "pt": p.part_family or "spares",
        "group": p.group_name or "",
        "price": p.price if show_prices else None,
        "mrp": p.mrp if show_prices else None,
        "por": p.price is None,                 # price on request
        "stock": p.stock or 0,
        "unit": p.unit or "piece",
        "size": p.size or "",
        "pack": p.pack or "",
        "colours": p.colours or "",
        "material": p.material or "",
        "note": p.description or "",
        "warranty": p.warranty or "",
        "weight": p.weight or "",
        "brands": [b.strip() for b in (p.brand_names or "").split(",") if b.strip()],
        "rating": p.rating or 0,
        "reviews": p.reviews or 0,
        "isNew": bool(p.is_new),
        "isBest": bool(p.is_best),
        "isFeat": bool(p.is_featured),
        "img": p.image_url or "",
        "imgs": [i.url for i in p.images],
    }


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    """Everything the storefront needs in one request."""
    show_prices = get_setting(db, "show_prices", "true") == "true"
    cats = db.query(Category).order_by(Category.sort_order, Category.name).all()
    brands = db.query(Brand).order_by(Brand.sort_order, Brand.name).all()
    prods = (db.query(Product)
             .filter(Product.visible == True)                      # noqa: E712
             .order_by(Product.sort_order, Product.id).all())
    payload = {
        "version": 1,
        "settings": {k: get_setting(db, k, "") for k in PUBLIC_SETTING_KEYS},
        "showPrices": show_prices,
        "categories": [{
            "id": c.id, "name": c.name, "code": c.code, "hue": c.hue,
            "desc": c.description, "popular": bool(c.popular),
            "icon": c.icon or "", "img": c.image_url or "",
        } for c in cats],
        "brands": [{"id": b.id, "name": b.name, "hue": b.hue} for b in brands],
        "products": [product_json(p, show_prices) for p in prods],
    }
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=60"})


@router.get("/products")
def products(q: str = "", cat: str = "", page: int = 1,
             per_page: int = Query(50, le=200), db: Session = Depends(get_db)):
    show_prices = get_setting(db, "show_prices", "true") == "true"
    query = db.query(Product).filter(Product.visible == True)       # noqa: E712
    if cat:
        query = query.filter(Product.category_id == cat)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Product.name.ilike(like), Product.sku.ilike(like)))
    total = query.count()
    rows = (query.order_by(Product.sort_order, Product.id)
            .offset((max(page, 1) - 1) * per_page).limit(per_page).all())
    return {"total": total, "page": page, "perPage": per_page,
            "products": [product_json(p, show_prices) for p in rows]}


@router.get("/products/{sku}")
def product_detail(sku: str, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.sku == sku, Product.visible == True).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Product not found")
    return product_json(p, get_setting(db, "show_prices", "true") == "true")


@router.get("/health")
def health():
    # deliberately does not expose catalogue counts (business info disclosure)
    return {"ok": True}
