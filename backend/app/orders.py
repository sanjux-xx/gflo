"""Storefront orders.

Public:
  POST /api/orders                 place an order (prices recomputed server-side)
Admin (any signed-in staff):
  GET  /admin/orders               list, filter by status
  GET  /admin/orders/{id}          order / invoice view (printable)
  POST /admin/orders/{id}/status   change status
  POST /admin/orders/{id}/note     save a staff note
  GET  /admin/orders/feed          JSON for the new-order notifier
"""
import json, os, re, time, threading
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import get_db
from .models import Order, Product
from .store import log, variants_list, get_setting
from . import security as sec
from .admin import render, flash, _user, _login_redirect, TEMPLATES
from .coupons import evaluate
from .posters import _OFFSET


def shop_time(ts) -> str:
    """Order time in the shop's local time (IST unless STORE_UTC_OFFSET_MINUTES says otherwise)."""
    return (ts + _OFFSET).strftime("%d %b %Y, %I:%M %p") if ts else ""


TEMPLATES.env.globals["shop_time"] = shop_time

public_router = APIRouter(prefix="/api", tags=["public"])
admin_router = APIRouter(prefix="/admin/orders", tags=["admin"])

STATUSES = {"new": "New", "confirmed": "Confirmed", "packed": "Packed", "shipped": "Shipped",
            "delivered": "Delivered", "cancelled": "Cancelled"}
SHIP = {"std": ("Standard delivery", 0), "exp": ("Express delivery", 99), "install": ("Expert install", 249)}
FREE_SHIP_FROM, BASE_SHIP = 4999, 99
MAX_LINES, MAX_QTY = 50, 99

# --- abuse guard (in-process) ------------------------------------------------
# Per mobile number: stops one person flooding the Orders page.
# Per connection (IP): a generous cap, because behind a hosting proxy every
# visitor can appear to come from the same address — a tight per-IP limit there
# would block real customers.
_RATE_WINDOW = 600                                                        # 10 minutes
_RATE_PHONE = int(os.environ.get("ORDER_RATE_PER_PHONE", "5") or 5)
_RATE_IP = int(os.environ.get("ORDER_RATE_LIMIT", "60") or 60)
_hits = defaultdict(deque)
_lock = threading.Lock()


def _rate_ok(*keys_limits) -> bool:
    """keys_limits: (key, limit) pairs. Counts the order only if every key is under its limit."""
    now = time.time()
    with _lock:
        for key, _ in keys_limits:
            q = _hits[key]
            while q and now - q[0] > _RATE_WINDOW:
                q.popleft()
        if any(len(_hits[k]) >= lim for k, lim in keys_limits):
            return False
        for key, _ in keys_limits:
            _hits[key].append(now)
        if len(_hits) > 5000:                     # forget idle keys so memory stays small
            for k in [k for k, q in _hits.items() if not q or now - q[-1] > _RATE_WINDOW]:
                del _hits[k]
        return True


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "size"


def _resolve(db: Session, sku: str):
    """'GF-B2-0101~45-x-12-mm' -> (product, size label|None, unit price|None) or None."""
    base, _, vslug = (sku or "").partition("~")
    p = db.query(Product).filter(Product.sku == base, Product.visible == True).first()  # noqa: E712
    if not p:
        return None
    sizes = variants_list(p.variants or "")
    if sizes:
        if not vslug:
            return None                       # a sized product must come with a size
        used = set()
        for v in sizes:                       # exactly the storefront's numbering rule
            b = _slug(v["label"]); code, n = b, 2
            while code in used:
                code = f"{b}-{n}"; n += 1
            used.add(code)
            if code == vslug:
                return p, v["label"], v["price"]
        return None
    if vslug:
        return None
    return p, None, p.price


def _clean(v, n):
    return " ".join(str(v or "").split())[:n]


def price_cart(db: Session, raw_items):
    """Server-side pricing of a cart: (lines, subtotal, quote_count, problems)."""
    merged = {}
    for it in (raw_items if isinstance(raw_items, list) else [])[:MAX_LINES]:
        if not isinstance(it, dict):
            continue
        sku = str(it.get("sku") or "")[:120]
        try:
            q = int(it.get("qty") or 0)
        except (TypeError, ValueError):
            q = 0
        if sku and 0 < q:
            merged[sku] = min(MAX_QTY, merged.get(sku, 0) + q)
    lines, sub, quote, problems = [], 0.0, 0, []
    for sku, q in merged.items():
        r = _resolve(db, sku)
        if not r:
            problems.append(f"An item in your cart is no longer available ({sku.split('~')[0]}).")
            continue
        p, size, price = r
        if (p.stock or 0) <= 0:
            problems.append(f"“{p.name}” is out of stock.")
            continue
        line = None if price is None else round(price * q, 2)
        if line is None:
            quote += 1
        else:
            sub += line
        lines.append({"sku": sku, "name": p.name, "size": size or "", "unit": p.unit or "piece",
                      "qty": q, "price": price, "line": line})
    return lines, sub, quote, problems


def cart_subtotal(db: Session, raw_items) -> float:
    return price_cart(db, raw_items)[1]


@public_router.post("/orders")
async def place_order(request: Request, db: Session = Depends(get_db)):
    # JSON only: a plain cross-site <form> can't send application/json, so other
    # websites can't submit orders on a visitor's behalf.
    if "application/json" not in (request.headers.get("content-type") or "").lower():
        return JSONResponse({"ok": False, "error": "Invalid request."}, 415)
    if int(request.headers.get("content-length") or 0) > 64_000:
        return JSONResponse({"ok": False, "error": "Request too large."}, 413)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid request."}, 400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "error": "Invalid request."}, 400)
    name, phone = _clean(data.get("name"), 80), re.sub(r"\D", "", str(data.get("phone") or ""))[-10:]
    addr, city = _clean(data.get("address"), 300), _clean(data.get("city"), 120)
    pin = re.sub(r"\D", "", str(data.get("pincode") or ""))
    if len(name) < 2:
        return JSONResponse({"ok": False, "error": "Please enter your name."}, 400)
    if not re.fullmatch(r"[6-9]\d{9}", phone):
        return JSONResponse({"ok": False, "error": "Please enter a valid 10-digit mobile number."}, 400)
    if len(addr) < 5 or len(city) < 2:
        return JSONResponse({"ok": False, "error": "Please enter your full address and city."}, 400)
    if not re.fullmatch(r"\d{6}", pin):
        return JSONResponse({"ok": False, "error": "Please enter a valid 6-digit pincode."}, 400)
    ship = data.get("ship") if data.get("ship") in SHIP else "std"
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return JSONResponse({"ok": False, "error": "Your cart is empty."}, 400)
    if len(raw_items) > MAX_LINES:
        return JSONResponse({"ok": False, "error": "Too many items in one order."}, 400)

    lines, sub, quote, problems = price_cart(db, raw_items)
    if problems:
        return JSONResponse({"ok": False, "error": " ".join(problems[:3]) + " Please update your cart."}, 409)
    if not lines:
        return JSONResponse({"ok": False, "error": "Your cart is empty."}, 400)

    code = _clean(data.get("coupon"), 24).upper()
    free_ship, disc = False, 0.0
    if code:
        cpn, disc, free_ship, cerr = evaluate(db, code, sub, phone)
        if cerr:
            # never charge more than the cart showed without telling the customer
            return JSONResponse({"ok": False, "coupon_error": True,
                                 "error": cerr + " Remove the coupon in your cart to continue."}, 409)
        code = cpn.code

    if not _rate_ok(("ip:" + sec.client_ip(request), _RATE_IP), ("ph:" + phone, _RATE_PHONE)):
        return JSONResponse({"ok": False, "error": "Too many orders in a short time. Please wait a few minutes, or call / WhatsApp us."}, 429)

    after = sub - disc
    base_ship = 0 if (free_ship or after >= FREE_SHIP_FROM) else BASE_SHIP
    shipping = base_ship + SHIP[ship][1]
    total = round(after + shipping, 2)

    o = Order(customer_name=name, phone=phone, address=addr, city=city, pincode=pin,
              ship_method=ship, payment="", coupon=code, items_json=json.dumps(lines),
              subtotal=sub, discount=disc, shipping=shipping, total=total, quote_items=quote,
              status="new", seen=False)
    db.add(o)
    db.flush()
    o.number = f"GF{1000 + o.id}"
    log(db, "storefront", "order", "order", o.number, f"{len(lines)} item(s), total {total}")
    db.commit()
    if get_setting(db, "show_prices", "true") != "true":       # catalogue mode: don't reveal prices
        return {"ok": True, "number": o.number, "items": [{"sku": l["sku"], "qty": l["qty"]} for l in lines]}
    return {"ok": True, "number": o.number, "total": total, "subtotal": sub, "discount": disc,
            "shipping": shipping, "quote_items": quote, "items": lines}


# ------------------------------------------------------------------- admin
def _order_view(o: Order) -> dict:
    try:
        items = json.loads(o.items_json or "[]")
    except ValueError:
        items = []
    return {"o": o, "items": items, "ship": SHIP.get(o.ship_method, ("", 0))[0],
            "status": STATUSES.get(o.status, o.status)}


@admin_router.get("", response_class=HTMLResponse)
@admin_router.get("/", response_class=HTMLResponse)
def orders_list(request: Request, status: str = "", page: int = 1, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    q = db.query(Order)
    if status in STATUSES:
        q = q.filter(Order.status == status)
    per = 50
    page = max(1, min(page, 10_000))
    total = q.count()
    rows = q.order_by(Order.id.desc()).offset((page - 1) * per).limit(per).all()
    counts = dict(db.query(Order.status, func.count(Order.id)).group_by(Order.status).all())
    return render(request, "orders.html", db=db, rows=[_order_view(o) for o in rows], statuses=STATUSES,
                  status=status, scounts=counts, total=total, page=page, pages=max(1, -(-total // per)),
                  msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""))


@admin_router.get("/feed")
def orders_feed(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return JSONResponse({"ok": False}, 401)
    latest = db.query(Order).order_by(Order.id.desc()).first()
    unseen = db.query(Order).filter(Order.seen == False).count()  # noqa: E712
    return JSONResponse({"ok": True, "unseen": unseen,
                         "latest": {"id": latest.id, "number": latest.number, "total": latest.total,
                                    "name": latest.customer_name} if latest else None},
                        headers={"Cache-Control": "no-store"})


@admin_router.get("/{oid}", response_class=HTMLResponse)
def order_detail(request: Request, oid: int, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    o = db.get(Order, oid) if 0 < oid < 2 ** 31 else None
    if not o:
        return flash("/admin/orders", err="That order doesn't exist.")
    if not o.seen:
        o.seen = True
        db.commit()
    return render(request, "order_detail.html", db=db, statuses=STATUSES, **_order_view(o),
                  msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""))


@admin_router.post("/{oid}/status")
async def order_status(request: Request, oid: int, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    o = db.get(Order, oid) if 0 < oid < 2 ** 31 else None
    if not o:
        return flash("/admin/orders", err="That order doesn't exist.")
    st = (await request.form()).get("status")
    if st not in STATUSES:
        return flash(f"/admin/orders/{oid}", err="Unknown status.")
    o.status, o.seen = st, True
    log(db, _user(request), "status", "order", o.number, st)
    db.commit()
    return flash(f"/admin/orders/{oid}", msg=f"Order {o.number} marked {STATUSES[st]}.")


@admin_router.post("/{oid}/note")
async def order_note(request: Request, oid: int, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    o = db.get(Order, oid) if 0 < oid < 2 ** 31 else None
    if not o:
        return flash("/admin/orders", err="That order doesn't exist.")
    o.note = str((await request.form()).get("note") or "")[:2000]
    log(db, _user(request), "note", "order", o.number)
    db.commit()
    return flash(f"/admin/orders/{oid}", msg="Note saved.")
