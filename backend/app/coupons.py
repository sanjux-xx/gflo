"""Coupons — created and switched on/off by the owner in the admin.

Admin (owner/admin accounts only):
  GET  /admin/coupons                 list + "create a coupon" form
  GET  /admin/coupons/{id}/edit       edit form
  POST /admin/coupons/new             create
  POST /admin/coupons/{id}/edit       save changes
  POST /admin/coupons/{id}/toggle     switch one coupon on / off
  POST /admin/coupons/{id}/delete     delete
  POST /admin/coupons/master          MASTER switch: coupons on / off for the whole shop
Public:
  POST /api/coupons/check             does this code work for this cart?

The same evaluate() is used by the cart check and by POST /api/orders, so the
discount shown in the cart is exactly the discount on the order.
"""
import datetime as dt
import os, re, time, threading
from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import get_db
from .models import Coupon, Order
from .store import log, get_setting, set_setting
from .admin import render, flash, _user, _elevated, _denied, _login_redirect
from .posters import shop_today
from . import security as sec

admin_router = APIRouter(prefix="/admin/coupons", tags=["admin"])
public_router = APIRouter(prefix="/api", tags=["public"])

KINDS = {"pct": "% off", "flat": "₹ off", "ship": "Free delivery"}
MASTER_KEY = "coupons_enabled"
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,23}$")


def coupons_enabled(db: Session) -> bool:
    """Master switch. OFF until the owner turns it on."""
    return get_setting(db, MASTER_KEY, "false") == "true"


def seed_examples(db: Session):
    """First run only: the three codes the shop used to have built in, saved as
    switched-OFF examples the owner can edit, switch on or delete."""
    if get_setting(db, "coupons_seeded", "") == "1":
        return
    if db.query(Coupon).count() == 0:
        db.add_all([
            Coupon(code="GFLO10", kind="pct", value=10, min_order=999, active=False, note="Example"),
            Coupon(code="FLAT100", kind="flat", value=100, min_order=599, active=False, note="Example"),
            Coupon(code="FREESHIP", kind="ship", value=0, min_order=0, active=False, note="Example"),
        ])
    set_setting(db, "coupons_seeded", "1")
    db.commit()


def times_used(db: Session, code: str, phone: str = "") -> int:
    q = db.query(func.count(Order.id)).filter(Order.coupon == code, Order.status != "cancelled")
    if phone:
        q = q.filter(Order.phone == phone)
    return q.scalar() or 0


def coupon_status(c: Coupon, today: Optional[dt.date] = None, used: Optional[int] = None) -> str:
    today = today or shop_today()
    if not c.active:
        return "off"
    if c.starts_on and today < c.starts_on:
        return "scheduled"
    if c.ends_on and today > c.ends_on:
        return "expired"
    if c.usage_limit and used is not None and used >= c.usage_limit:
        return "used up"
    return "live"


def describe(c: Coupon) -> str:
    def r(v):
        return f"₹{v:,.0f}"
    if c.kind == "pct":
        s = f"{c.value:g}% off" + (f" (max {r(c.max_discount)})" if c.max_discount else "")
    elif c.kind == "flat":
        s = f"{r(c.value)} off"
    else:
        s = "Free delivery"
    return s + (f" on orders over {r(c.min_order)}" if c.min_order else "")


def evaluate(db: Session, code: str, subtotal: float, phone: str = ""):
    """(coupon, discount, free_ship, error). error is a customer-facing sentence."""
    code = (code or "").strip().upper()[:24]
    if not code:
        return None, 0.0, False, ""
    if not coupons_enabled(db) or get_setting(db, "show_prices", "true") != "true":
        return None, 0.0, False, "Coupons aren't available right now."
    c = db.query(Coupon).filter(Coupon.code == code).first()
    if not c:
        return None, 0.0, False, f"“{code}” isn't a valid coupon."
    st = coupon_status(c, used=times_used(db, c.code) if c.usage_limit else None)
    if st == "off":
        return None, 0.0, False, f"“{code}” isn't a valid coupon."
    if st == "scheduled":
        return None, 0.0, False, f"“{code}” starts on {c.starts_on.strftime('%d %b %Y')}."
    if st == "expired":
        return None, 0.0, False, f"“{code}” has expired."
    if st == "used up":
        return None, 0.0, False, f"“{code}” has been fully used."
    if c.min_order and subtotal < c.min_order:
        return None, 0.0, False, f"“{code}” needs a minimum order of ₹{c.min_order:,.0f}."
    if c.once_per_phone and phone and times_used(db, c.code, phone) > 0:
        return None, 0.0, False, f"“{code}” has already been used with this mobile number."
    if c.kind == "pct":
        disc = round(subtotal * (c.value or 0) / 100)
        if c.max_discount:
            disc = min(disc, c.max_discount)
    elif c.kind == "flat":
        disc = c.value or 0
    else:
        disc = 0
    disc = float(max(0, min(disc, subtotal)))
    return c, disc, c.kind == "ship", ""


def public_info(c: Coupon, disc: float, free_ship: bool) -> dict:
    return {"code": c.code, "kind": c.kind, "value": c.value, "min": c.min_order or 0,
            "max": c.max_discount, "discount": disc, "freeShip": free_ship, "label": describe(c)}


# --- guard against guessing codes ---------------------------------------------
_hits = defaultdict(deque)
_lock = threading.Lock()


# generous: behind a hosting proxy all visitors may share one address
_CHECK_LIMIT = int(os.environ.get("COUPON_CHECK_LIMIT", "300") or 300)


def _check_rate_ok(ip: str, limit: int = 0, window: int = 600) -> bool:
    limit = limit or _CHECK_LIMIT
    now = time.time()
    with _lock:
        q = _hits[ip]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(_hits) > 5000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > window]:
                del _hits[k]
        return True


@public_router.post("/coupons/check")
async def coupon_check(request: Request, db: Session = Depends(get_db)):
    from .orders import cart_subtotal                     # late import: orders imports us
    if "application/json" not in (request.headers.get("content-type") or "").lower():
        return JSONResponse({"ok": False, "error": "Invalid request."}, 415)
    try:
        data = await request.json()
        assert isinstance(data, dict)
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid request."}, 400)
    if not _check_rate_ok("ip:" + sec.client_ip(request)):
        return JSONResponse({"ok": False, "error": "Too many tries. Please wait a few minutes."}, 429)
    sub = cart_subtotal(db, data.get("items"))
    phone = re.sub(r"\D", "", str(data.get("phone") or ""))[-10:]
    c, disc, free_ship, err = evaluate(db, str(data.get("code") or ""), sub, phone)
    if err or not c:
        return JSONResponse({"ok": False, "error": err or "Enter a coupon code."}, 200)
    return {"ok": True, **public_info(c, disc, free_ship)}


# ------------------------------------------------------------------ admin
def _gate(request: Request):
    if not _user(request):
        return _login_redirect(request)
    if not _elevated(request):
        return _denied()
    return None


def _num(raw, label, allow_blank=True, integer=False):
    v = (raw or "").strip().replace(",", "").replace("₹", "")
    if not v:
        return (None, "") if allow_blank else (None, f"{label} is required.")
    try:
        n = int(v) if integer else float(v)
    except ValueError:
        return None, f"{label} must be a number."
    if n < 0:
        return None, f"{label} can't be negative."
    return n, ""


def _date(raw, label):
    v = (raw or "").strip()
    if not v:
        return None, ""
    try:
        return dt.date.fromisoformat(v), ""
    except ValueError:
        return None, f"{label} isn't a valid date."


def _apply(db: Session, c: Coupon, form) -> str:
    code = re.sub(r"\s+", "", (form.get("code") or "")).upper()
    if not CODE_RE.match(code):
        return "Coupon code: 2–24 letters or numbers (dash and underscore allowed), e.g. DIWALI10."
    other = db.query(Coupon).filter(Coupon.code == code).first()
    if other and other.id != c.id:
        return f"A coupon called {code} already exists."
    kind = form.get("kind") if form.get("kind") in KINDS else "pct"
    value, err = _num(form.get("value"), "Discount", allow_blank=(kind == "ship"))
    if err:
        return err
    value = value or 0
    if kind == "pct" and not (0 < value <= 100):
        return "Percent off must be between 1 and 100."
    if kind == "flat" and value <= 0:
        return "Enter how many rupees off, e.g. 100."
    min_order, err = _num(form.get("min_order"), "Minimum order")
    if err:
        return err
    max_disc, err = _num(form.get("max_discount"), "Maximum discount")
    if err:
        return err
    limit, err = _num(form.get("usage_limit"), "Usage limit", integer=True)
    if err:
        return err
    starts, err = _date(form.get("starts_on"), "Start date")
    if err:
        return err
    ends, err = _date(form.get("ends_on"), "End date")
    if err:
        return err
    if starts and ends and ends < starts:
        return "The end date is before the start date."
    c.code, c.kind, c.value = code, kind, value
    c.min_order = min_order or 0
    c.max_discount = max_disc if (kind == "pct" and max_disc) else None
    c.usage_limit = limit or None
    c.starts_on, c.ends_on = starts, ends
    c.once_per_phone = bool(form.get("once_per_phone"))
    c.active = bool(form.get("active"))
    c.note = (form.get("note") or "").strip()[:200]
    return ""


def _page(request: Request, db: Session, edit: Optional[Coupon] = None):
    today = shop_today()
    rows = db.query(Coupon).order_by(Coupon.active.desc(), Coupon.updated_at.desc(), Coupon.id.desc()).all()
    used = {c.id: times_used(db, c.code) for c in rows}
    return render(request, "coupons.html", db=db, rows=rows, edit=edit, kinds=KINDS, used=used,
                  status={c.id: coupon_status(c, today, used[c.id]) for c in rows},
                  describe=describe, master_on=coupons_enabled(db), today=today.isoformat(),
                  msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""))


@admin_router.get("", response_class=HTMLResponse)
@admin_router.get("/", response_class=HTMLResponse)
def coupons_list(request: Request, db: Session = Depends(get_db)):
    return _gate(request) or _page(request, db)


@admin_router.get("/{cid}/edit", response_class=HTMLResponse)
def coupon_edit_form(request: Request, cid: int, db: Session = Depends(get_db)):
    stop = _gate(request)
    if stop:
        return stop
    c = db.get(Coupon, cid) if 0 < cid < 2 ** 31 else None
    if not c:
        return flash("/admin/coupons", err="That coupon doesn't exist.")
    return _page(request, db, edit=c)


@admin_router.post("/new")
async def coupon_create(request: Request, db: Session = Depends(get_db)):
    stop = _gate(request)
    if stop:
        return stop
    c = Coupon()
    err = _apply(db, c, await request.form())
    if err:
        return flash("/admin/coupons", err=err)
    db.add(c)
    log(db, _user(request), "create", "coupon", c.code, describe(c))
    db.commit()
    return flash("/admin/coupons", msg=f"Coupon {c.code} created" + ("." if c.active else " (switched off)."))


@admin_router.post("/master")
async def coupon_master(request: Request, db: Session = Depends(get_db)):
    stop = _gate(request)
    if stop:
        return stop
    on = (await request.form()).get("state") == "on"
    set_setting(db, MASTER_KEY, "true" if on else "false")
    log(db, _user(request), "update", "coupon", "", f"coupons master switch {'ON' if on else 'OFF'}")
    db.commit()
    return flash("/admin/coupons", msg="Coupons are ON — the coupon box shows in the cart." if on
                 else "Coupons are OFF — the coupon box is hidden and no code works.")


@admin_router.post("/{cid}/edit")
async def coupon_save(request: Request, cid: int, db: Session = Depends(get_db)):
    stop = _gate(request)
    if stop:
        return stop
    c = db.get(Coupon, cid) if 0 < cid < 2 ** 31 else None
    if not c:
        return flash("/admin/coupons", err="That coupon doesn't exist.")
    old = c.code
    err = _apply(db, c, await request.form())
    if err:
        db.rollback()
        return flash(f"/admin/coupons/{cid}/edit", err=err)
    log(db, _user(request), "update", "coupon", c.code, describe(c) + (f" (renamed from {old})" if old != c.code else ""))
    db.commit()
    return flash("/admin/coupons", msg=f"Coupon {c.code} saved.")


@admin_router.post("/{cid}/toggle")
async def coupon_toggle(request: Request, cid: int, db: Session = Depends(get_db)):
    stop = _gate(request)
    if stop:
        return stop
    c = db.get(Coupon, cid) if 0 < cid < 2 ** 31 else None
    if not c:
        return flash("/admin/coupons", err="That coupon doesn't exist.")
    c.active = not c.active
    log(db, _user(request), "update", "coupon", c.code, "switched " + ("on" if c.active else "off"))
    db.commit()
    return flash("/admin/coupons", msg=f"{c.code} switched {'ON' if c.active else 'OFF'}.")


@admin_router.post("/{cid}/delete")
async def coupon_delete(request: Request, cid: int, db: Session = Depends(get_db)):
    stop = _gate(request)
    if stop:
        return stop
    c = db.get(Coupon, cid) if 0 < cid < 2 ** 31 else None
    if not c:
        return flash("/admin/coupons", err="That coupon doesn't exist.")
    code = c.code
    db.delete(c)
    log(db, _user(request), "delete", "coupon", code)
    db.commit()
    return flash("/admin/coupons", msg=f"Coupon {code} deleted. Past orders keep their discount.")
