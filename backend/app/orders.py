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
import datetime as dt
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


def shop_time_iso(v: str) -> str:
    try:
        return shop_time(dt.datetime.fromisoformat(v.rstrip("Z")))
    except (ValueError, AttributeError):
        return ""


TEMPLATES.env.globals["shop_time_iso"] = shop_time_iso

public_router = APIRouter(prefix="/api", tags=["public"])
admin_router = APIRouter(prefix="/admin/orders", tags=["admin"])

STATUSES = {"new": "New", "confirmed": "Confirmed", "packed": "Packed", "shipped": "Shipped",
            "delivered": "Delivered", "cancelled": "Cancelled"}
SHIP = {"std": ("Standard delivery", 0), "exp": ("Express delivery", 99), "install": ("Expert install", 249)}
FREE_SHIP_FROM, BASE_SHIP = 4999, 99
MAX_LINES, MAX_QTY = 50, 99
# all 28 states + 8 union territories; the checkout shows them as a list to pick from
STATES = ['Andaman and Nicobar Islands', 'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chandigarh', 'Chhattisgarh', 'Dadra and Nagar Haveli and Daman and Diu', 'Delhi', 'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jammu and Kashmir', 'Jharkhand', 'Karnataka', 'Kerala', 'Ladakh', 'Lakshadweep', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram', 'Nagaland', 'Odisha', 'Puducherry', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal']

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


def _iso(ts) -> str:
    return ts.replace(microsecond=0).isoformat() + "Z" if ts else ""


def _log_status(o: Order, status: str):
    """Remember when each status was first reached (shown to the customer)."""
    try:
        log_ = json.loads(o.status_log or "{}")
    except ValueError:
        log_ = {}
    if not log_.get("new") and o.created_at:
        log_["new"] = _iso(o.created_at)
    log_[status] = _iso(dt.datetime.utcnow())
    o.status_log = json.dumps(log_)


def status_times(o: Order) -> dict:
    try:
        log_ = json.loads(o.status_log or "{}")
    except ValueError:
        log_ = {}
    log_.setdefault("new", _iso(o.created_at))
    return log_


def _clean(v, n):
    return " ".join(str(v or "").split())[:n]


MAX_BODY = 64_000
NO_STORE = {"Cache-Control": "no-store"}


async def read_json(request: Request):
    """(data, error_response). Reads at most MAX_BODY bytes however the body is
    sent (a chunked upload has no Content-Length to check), and never lets odd
    JSON (very deep nesting, wrong types) turn into a server error."""
    if "application/json" not in (request.headers.get("content-type") or "").lower():
        return None, JSONResponse({"ok": False, "error": "Invalid request."}, 415)
    try:
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return None, JSONResponse({"ok": False, "error": "Request too large."}, 413)
    except ValueError:
        return None, JSONResponse({"ok": False, "error": "Invalid request."}, 400)
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_BODY:
            return None, JSONResponse({"ok": False, "error": "Request too large."}, 413)
    try:
        data = json.loads(bytes(buf))
    except (ValueError, RecursionError):
        return None, JSONResponse({"ok": False, "error": "Invalid request."}, 400)
    if not isinstance(data, dict):
        return None, JSONResponse({"ok": False, "error": "Invalid request."}, 400)
    return data, None


def _text(v) -> str:
    """Only plain text / numbers count as text; objects and lists become ""."""
    return str(v) if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def price_cart(db: Session, raw_items):
    """Server-side pricing of a cart: (lines, subtotal, quote_count, problems)."""
    merged = {}
    for it in (raw_items if isinstance(raw_items, list) else [])[:MAX_LINES]:
        if not isinstance(it, dict):
            continue
        sku = _text(it.get("sku"))[:120]
        raw_q = it.get("qty")
        try:
            q = int(raw_q) if isinstance(raw_q, (int, float, str)) and not isinstance(raw_q, bool) else 0
        except (TypeError, ValueError, OverflowError):
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
        if q > p.stock:
            problems.append(f"Only {p.stock} of “{p.name}” left in stock.")
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
    data, bad = await read_json(request)
    if bad:
        return bad
    name, phone = _clean(_text(data.get("name")), 80), re.sub(r"\D", "", _text(data.get("phone")))[-10:]
    addr, city = _clean(_text(data.get("address")), 300), _clean(_text(data.get("city")), 60)
    state = _clean(_text(data.get("state")), 60)
    pin = re.sub(r"\D", "", _text(data.get("pincode")))
    if len(name) < 2:
        return JSONResponse({"ok": False, "error": "Please enter your name."}, 400)
    if not re.fullmatch(r"[6-9]\d{9}", phone):
        return JSONResponse({"ok": False, "error": "Please enter a valid 10-digit mobile number."}, 400)
    if len(addr) < 5:
        return JSONResponse({"ok": False, "error": "Please enter your full address."}, 400)
    if (len(city) < 2 or not city[0].isalpha() or sum(ch.isalpha() for ch in city) < 2
            or any(ch in '<>"`{}[]\\|;=*@#$%^~' for ch in city)):
        return JSONResponse({"ok": False, "error": "Please enter a valid city or town name."}, 400)
    state = next((st for st in STATES if st.lower() == state.lower()), "")
    if not state:
        return JSONResponse({"ok": False, "error": "Please select your state from the list."}, 400)
    city = f"{city}, {state}"
    if not re.fullmatch(r"\d{6}", pin):
        return JSONResponse({"ok": False, "error": "Please enter a valid 6-digit pincode."}, 400)
    ship = "std"            # one delivery option only (no express / install choice)
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

    code = _clean(_text(data.get("coupon")), 24).upper()
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
    _log_status(o, "new")
    db.add(o)
    db.flush()
    o.number = f"GF{1000 + o.id}"
    log(db, "storefront", "order", "order", o.number, f"{len(lines)} item(s), total {total}")
    db.commit()
    if get_setting(db, "show_prices", "true") != "true":       # catalogue mode: don't reveal prices
        return {"ok": True, "number": o.number, "items": [{"sku": l["sku"], "qty": l["qty"]} for l in lines]}
    return {"ok": True, "number": o.number, "total": total, "subtotal": sub, "discount": disc,
            "shipping": shipping, "quote_items": quote, "items": lines}


# --- customer: order status --------------------------------------------------
_track_hits = defaultdict(deque)


@public_router.get("/orders/{number}/status")
def order_track(request: Request, number: str, phone: str = "", db: Session = Depends(get_db)):
    """The live status of one order for the customer's order page. The mobile
    number on the order must match, so order numbers can't be used to look up
    other people's orders."""
    now = time.time()
    with _lock:
        q = _track_hits["ip:" + sec.client_ip(request)]
        while q and now - q[0] > 600:
            q.popleft()
        if len(q) >= 300:
            return JSONResponse({"ok": False, "error": "Too many requests."}, 429, headers=NO_STORE)
        q.append(now)
        if len(_track_hits) > 5000:                     # forget idle visitors
            for k in [k for k, v in _track_hits.items() if not v or now - v[-1] > 600]:
                del _track_hits[k]
    ph = re.sub(r"\D", "", phone or "")[-10:]
    o = db.query(Order).filter(Order.number == (number or "").strip().upper()[:24]).first() if ph else None
    if not o or o.phone != ph:
        return JSONResponse({"ok": False, "error": "Order not found."}, 404, headers={"Cache-Control": "no-store"})
    return JSONResponse({"ok": True, "number": o.number, "status": o.status,
                         "label": STATUSES.get(o.status, o.status), "times": status_times(o)},
                        headers={"Cache-Control": "no-store"})


# --- customer: find my orders by mobile number ----------------------------------
# Shows status, date, items and total only — never the name or address — and
# is rate-limited per connection and per number so numbers can't be trawled.
_LOOKUP_IP, _LOOKUP_PHONE = 20, 10        # per 10 minutes


@public_router.get("/orders/lookup")
def order_lookup(request: Request, phone: str = "", db: Session = Depends(get_db)):
    ph = re.sub(r"\D", "", phone or "")[-10:]
    if not re.fullmatch(r"[6-9]\d{9}", ph):
        return JSONResponse({"ok": False, "error": "Enter the 10-digit mobile number you ordered with."}, 400, headers=NO_STORE)
    now = time.time()
    with _lock:
        for key, lim in (("lk-ip:" + sec.client_ip(request), _LOOKUP_IP), ("lk-ph:" + ph, _LOOKUP_PHONE)):
            q = _track_hits[key]
            while q and now - q[0] > 600:
                q.popleft()
            if len(q) >= lim:
                return JSONResponse({"ok": False, "error": "Too many searches. Please try again in a few minutes, or call us."}, 429, headers=NO_STORE)
        for key in ("lk-ip:" + sec.client_ip(request), "lk-ph:" + ph):
            _track_hits[key].append(now)
    rows = db.query(Order).filter(Order.phone == ph).order_by(Order.id.desc()).limit(20).all()
    show_prices = get_setting(db, "show_prices", "true") == "true"
    out = []
    for o in rows:
        try:
            items = json.loads(o.items_json or "[]")
        except ValueError:
            items = []
        out.append({"number": o.number, "date": _iso(o.created_at), "status": o.status,
                    "label": STATUSES.get(o.status, o.status), "times": status_times(o),
                    "total": o.total if show_prices else None, "quote": o.quote_items or 0,
                    "items": [{"name": i.get("name", ""), "size": i.get("size", ""), "qty": i.get("qty", 0)} for i in items][:30]})
    return JSONResponse({"ok": True, "orders": out}, headers={"Cache-Control": "no-store"})


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
    return render(request, "order_detail.html", db=db, statuses=STATUSES, times=status_times(o), **_order_view(o),
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
    _log_status(o, st)
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
