"""Public read-only JSON API consumed by the storefront."""
import hashlib, json, threading, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from .db import get_db
from .models import Product, Category, Brand, Setting
from .store import get_setting, PUBLIC_SETTING_KEYS, variants_list

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
        "variants": [{"label": v["label"], "price": v["price"] if show_prices else None}
                     for v in variants_list(getattr(p, "variants", "") or "")],
    }


# --- catalogue cache -----------------------------------------------------------
# Building the catalogue touches every product and its photos. It used to run
# one database query per product (900+), which on a hosted database that sits
# on another machine took ~3 seconds per visitor. Now it is built with a few
# queries and kept in memory; any change made in the admin (every admin form
# post) clears it, and it is rebuilt at least every 5 minutes regardless.
_CACHE = {"version": None, "at": 0.0, "body": b"", "etag": ""}
_CACHE_LOCK = threading.Lock()
_VERSION = [0]
CACHE_SECONDS = 300


def invalidate_catalog():
    """Call after anything the storefront shows may have changed."""
    _VERSION[0] += 1


def _settings(db: Session) -> dict:
    return {row.key: row.value or "" for row in db.query(Setting).all()}


def _build_catalog(db: Session) -> bytes:
    st = _settings(db)
    show_prices = st.get("show_prices", "true") == "true"
    cats = db.query(Category).order_by(Category.sort_order, Category.name).all()
    brands = db.query(Brand).order_by(Brand.sort_order, Brand.name).all()
    prods = (db.query(Product).options(selectinload(Product.images))
             .filter(Product.visible == True)                      # noqa: E712
             .order_by(Product.sort_order, Product.id).all())
    payload = {
        "version": 1,
        "settings": {k: st.get(k, "") for k in PUBLIC_SETTING_KEYS},
        "showPrices": show_prices,
        "couponsEnabled": show_prices and st.get("coupons_enabled", "false") == "true",
        "categories": [{
            "id": c.id, "name": c.name, "code": c.code, "hue": c.hue,
            "desc": c.description, "popular": bool(c.popular),
            "icon": c.icon or "", "img": c.image_url or "",
        } for c in cats],
        "brands": [{"id": b.id, "name": b.name, "hue": b.hue} for b in brands],
        "products": [product_json(p, show_prices) for p in prods],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@router.get("/catalog")
def catalog(request: Request, db: Session = Depends(get_db)):
    """Everything the storefront needs in one request (served from memory)."""
    now = time.time()
    with _CACHE_LOCK:
        fresh = _CACHE["version"] == _VERSION[0] and now - _CACHE["at"] < CACHE_SECONDS and _CACHE["body"]
        if not fresh:
            body = _build_catalog(db)
            _CACHE.update(version=_VERSION[0], at=now, body=body,
                          etag='"' + hashlib.sha1(body).hexdigest()[:20] + '"')
        body, etag = _CACHE["body"], _CACHE["etag"]
    headers = {"Cache-Control": "public, max-age=60", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@router.get("/products")
def products(q: str = "", cat: str = "", page: int = Query(1, ge=1),
             per_page: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    show_prices = get_setting(db, "show_prices", "true") == "true"
    query = db.query(Product).filter(Product.visible == True)       # noqa: E712
    if cat:
        query = query.filter(Product.category_id == cat)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Product.name.ilike(like), Product.sku.ilike(like)))
    total = query.count()
    rows = (query.options(selectinload(Product.images)).order_by(Product.sort_order, Product.id)
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
