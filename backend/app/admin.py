"""Admin website: login, dashboard, product CRUD, categories, brands, import, settings."""
import csv, io, math, os
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from .db import get_db
from .models import Product, ProductImage, Category, Brand, AdminUser, AuditLog
from . import security as sec
from .store import (get_setting, set_setting, log, save_upload, delete_media,
                    unique_sku, slugify, DEFAULT_SETTINGS, parse_money, parse_qty,
                    clamp_money, csv_safe)

router = APIRouter(prefix="/admin", tags=["admin"])
TEMPLATES = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
TEMPLATES.env.globals["money"] = lambda v: "—" if v in (None, "") else f"₹{v:,.2f}".replace(".00", "")


def safe_next(target: str, fallback: str = "/admin") -> str:
    """Only ever redirect to our own admin pages. An unvalidated ?next= let a
    signed-in admin be bounced to an attacker's site (open redirect)."""
    t = (target or "").strip()
    if not t.startswith("/admin"):
        return fallback
    if t.startswith("//") or "\\" in t or "\n" in t or "\r" in t:
        return fallback
    return t

# ------------------------------------------------------------------- icon set
# Inline stroke icons rendered server-side, so the console needs no icon font,
# sprite file or CDN and looks identical offline.
ICON_PATHS = {
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "box": '<path d="M21 8l-9-5-9 5v8l9 5 9-5V8z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v8"/>',
    "tag": '<path d="M20.6 13.4l-7.2 7.2a2 2 0 01-2.8 0l-7.2-7.2A2 2 0 013 12V5a2 2 0 012-2h7a2 2 0 011.4.6l7.2 7.2a2 2 0 010 2.6z"/><circle cx="7.5" cy="7.5" r="1.2"/>',
    "layers": '<path d="M12 2l9 5-9 5-9-5 9-5z"/><path d="M3 12l9 5 9-5"/><path d="M3 17l9 5 9-5"/>',
    "upload": '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/>',
    "download": '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/>',
    "cog": '<circle cx="12" cy="12" r="3.2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9L7 7M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "ext": '<path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><path d="M15 3h6v6"/><path d="M10 14L21 3"/>',
    "logout": '<path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>',
    "menu": '<path d="M3 12h18M3 6h18M3 18h18"/>',
    "chev-r": '<path d="M9 18l6-6-6-6"/>',
    "chev-l": '<path d="M15 18l-6-6 6-6"/>',
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/>',
    "x": '<path d="M18 6L6 18M6 6l12 12"/>',
    "alert": '<path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L14.7 3.9a2 2 0 00-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
    "eye": '<path d="M1.5 12S5 5.5 12 5.5 22.5 12 22.5 12 19 18.5 12 18.5 1.5 12 1.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M17.9 17.9A10.4 10.4 0 0112 19.5C5 19.5 1.5 12 1.5 12a18.6 18.6 0 015.1-6"/><path d="M9.9 5.7A10 10 0 0112 5.5c7 0 10.5 6.5 10.5 6.5a18.6 18.6 0 01-2.2 3.2"/><path d="M10 10a3 3 0 004 4"/><path d="M2 2l20 20"/>',
    "lock": '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 018 0v3"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>',
    "trash": '<path d="M3 6h18"/><path d="M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>',
    "edit": '<path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.1 2.1 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.4"/><path d="M21 15l-5-5L5 21"/>',
    "rupee": '<path d="M6 3h12M6 8h12M16 21L8 12h3a4.5 4.5 0 000-9"/>',
    "star": '<path d="M12 2l3.1 6.3 7 1-5 4.9 1.2 7-6.3-3.3L5.7 21l1.2-7-5-4.9 7-1L12 2z"/>',
    "off": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "users": '<path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 00-3-3.9"/><path d="M16 3.1a4 4 0 010 7.8"/>',
    "store": '<path d="M3 9l1.5-5h15L21 9"/><path d="M3 9h18v11a1 1 0 01-1 1H4a1 1 0 01-1-1V9z"/><path d="M9 21v-6h6v6"/>',
    "filter": '<path d="M4 4h16l-6 8v6l-4 2v-8L4 4z"/>',
    "save": '<path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
    "package": '<path d="M21 16V8a2 2 0 00-1-1.7l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.7l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/><path d="M3.3 7L12 12l8.7-5M12 22V12"/>',
    "sliders": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
}


def icon(name: str, size: int = 18) -> Markup:
    return Markup(
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f'{ICON_PATHS.get(name, "")}</svg>'
    )


TEMPLATES.env.globals["icon"] = icon

PART_FAMILIES = ["spares", "blade", "motor", "bearing", "capacitor", "switch", "knob",
                 "gasket", "seal", "filter", "pump", "gear", "belt", "pcb", "valve",
                 "sensor", "body", "screw-kit", "clip-kit", "relay", "piston", "carbon-brush"]
UNITS = ["piece", "packet", "box", "coil", "kg", "metre", "1000 pcs", "set", "pair"]


# ------------------------------------------------------------------ plumbing
def _user(request: Request) -> Optional[str]:
    return sec.current_user(request)


def _login_redirect(request: Request) -> RedirectResponse:
    nxt = request.url.path
    return RedirectResponse(f"/admin/login?next={nxt}", status_code=303)


def sidebar_counts(db: Session) -> dict:
    """Live badge numbers for the sidebar — cheap COUNTs on an indexed table."""
    if db is None:
        return {}
    return {
        "products": db.query(Product).count(),
        "categories": db.query(Category).count(),
        "brands": db.query(Brand).count(),
        "no_price": db.query(Product).filter(Product.price == None).count(),      # noqa: E711
        "out_of_stock": db.query(Product).filter(Product.stock <= 0).count(),
        "hidden": db.query(Product).filter(Product.visible == False).count(),     # noqa: E712
    }


def render(request: Request, template: str, **ctx) -> HTMLResponse:
    db = ctx.get("db")
    ctx.setdefault("csrf_token", getattr(request.state, "csrf", "")
                   or request.cookies.get(sec.CSRF_COOKIE, ""))
    ctx.setdefault("store_name", get_setting(db, "store_name", "G-FLO") if db is not None else "G-FLO")
    ctx.setdefault("username", _user(request))
    ctx.setdefault("path", request.url.path)
    if ctx.get("username"):
        ctx.setdefault("counts", sidebar_counts(db))
    ctx.setdefault("counts", {})
    ctx.pop("db", None)
    return TEMPLATES.TemplateResponse(request, template, ctx)


def flash(url: str, msg: str = "", err: str = "") -> RedirectResponse:
    from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse
    parts = list(urlparse(url))
    q = dict(parse_qsl(parts[4]))
    if msg:
        q["msg"] = msg
    if err:
        q["err"] = err
    parts[4] = urlencode(q)
    return RedirectResponse(urlunparse(parts), status_code=303)


def _elevated(request: Request) -> bool:
    """Owner/admin only: account management, store settings, bulk import/export."""
    return sec.is_elevated(sec.current_admin(request))


def _denied(back: str = "/admin"):
    return flash(back, err="Your account doesn't have permission for that. Ask the owner.")


def require(request: Request):
    """Dependency: returns username or raises a redirect via RequireLogin."""
    user = _user(request)
    return user


# ------------------------------------------------------------------- login
@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/admin", db: Session = Depends(get_db)):
    if _user(request):
        return RedirectResponse(safe_next(next), status_code=303)
    return render(request, "login.html", db=db, next=safe_next(next),
                  err=request.query_params.get("err", ""),
                  msg=request.query_params.get("msg", ""), username=None)


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...),
                 next: str = Form("/admin"), db: Session = Depends(get_db)):
    ip = sec.client_ip(request)
    keys = [f"ip:{ip}", f"user:{username.lower()}"]
    wait = sec.login_blocked(keys)
    if wait:
        mins = max(1, wait // 60)
        return render(request, "login.html", db=db, next=next, username=None,
                      err=f"Too many failed attempts. Try again in about {mins} minute(s).")
    user = db.query(AdminUser).filter(func.lower(AdminUser.username) == username.lower().strip()).first()
    # burn equivalent scrypt work when the account doesn't exist, so response
    # time can't be used to enumerate valid usernames
    ok = sec.verify_password(password, user.password_hash) if user else sec.dummy_verify(password)
    if not user or not ok:
        sec.record_failure(keys)
        log(db, username.strip()[:64], "login_failed", "admin_user", detail=f"ip={ip}")
        db.commit()
        return render(request, "login.html", db=db, next=next, username=None,
                      err="Wrong username or password.")
    sec.clear_failures(keys)
    from .models import now as _now
    user.last_login = _now()
    log(db, user.username, "login", "admin_user", user.id, f"ip={ip}")
    db.commit()
    resp = RedirectResponse(safe_next(next), status_code=303)
    resp.set_cookie(sec.SESSION_COOKIE,
                    sec.make_session(user.username, user.token_version or 0),
                    **sec.cookie_kwargs(request))
    # fresh CSRF token bound to the new session
    resp.set_cookie(sec.CSRF_COOKIE, sec.new_csrf_token(), **sec.csrf_cookie_kwargs(request))
    return resp


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    # bump token_version so the cookie we just dropped can never be replayed
    me = _user(request)
    if me:
        user = db.query(AdminUser).filter(func.lower(AdminUser.username) == me.lower()).first()
        if user:
            user.token_version = (user.token_version or 0) + 1
            log(db, me, "logout", "admin_user", user.id)
            db.commit()
    resp = RedirectResponse("/admin/login?msg=Signed+out", status_code=303)
    resp.delete_cookie(sec.SESSION_COOKIE, path="/")
    return resp


# --------------------------------------------------------------- dashboard
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    total = db.query(Product).count()
    stats = {
        "total": total,
        "visible": db.query(Product).filter(Product.visible == True).count(),      # noqa: E712
        "hidden": db.query(Product).filter(Product.visible == False).count(),      # noqa: E712
        "out_of_stock": db.query(Product).filter(Product.stock <= 0).count(),
        "no_price": db.query(Product).filter(Product.price == None).count(),       # noqa: E711
        "no_photo": db.query(Product).filter(or_(Product.image_url == "", Product.image_url == None)).count(),  # noqa: E711
        "categories": db.query(Category).count(),
        "brands": db.query(Brand).count(),
    }
    per_cat = (db.query(Category.name, func.count(Product.id), Category.id)
               .outerjoin(Product, Product.category_id == Category.id)
               .group_by(Category.id).order_by(func.count(Product.id).desc()).all())
    recent = db.query(AuditLog).order_by(AuditLog.ts.desc()).limit(12).all()
    return render(request, "dashboard.html", db=db, stats=stats, per_cat=per_cat, recent=recent,
                  msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""))


# ---------------------------------------------------------------- products
@router.get("/products", response_class=HTMLResponse)
def products_list(request: Request, q: str = "", cat: str = "", stock: str = "",
                  price: str = "", vis: str = "", sort: str = "name",
                  page: int = 1, per_page: int = 40, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    query = db.query(Product)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Product.name.ilike(like), Product.sku.ilike(like),
                                 Product.group_name.ilike(like), Product.size.ilike(like)))
    if cat:
        query = query.filter(Product.category_id == cat)
    if stock == "out":
        query = query.filter(Product.stock <= 0)
    elif stock == "low":
        query = query.filter(Product.stock > 0, Product.stock < 10)
    if price == "none":
        query = query.filter(Product.price == None)                    # noqa: E711
    elif price == "set":
        query = query.filter(Product.price != None)                    # noqa: E711
    if vis == "hidden":
        query = query.filter(Product.visible == False)                 # noqa: E712
    elif vis == "visible":
        query = query.filter(Product.visible == True)                  # noqa: E712

    order = {"name": Product.name, "sku": Product.sku,
             "price_asc": Product.price.asc(), "price_desc": Product.price.desc(),
             "stock": Product.stock.asc(), "newest": Product.id.desc(),
             "updated": Product.updated_at.desc()}.get(sort, Product.name)
    total = query.count()
    per_page = max(10, min(per_page, 200))
    pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, pages))
    rows = query.order_by(order).offset((page - 1) * per_page).limit(per_page).all()
    cats = db.query(Category).order_by(Category.name).all()
    return render(request, "products.html", db=db, rows=rows, cats=cats, total=total,
                  page=page, pages=pages, per_page=per_page,
                  f={"q": q, "cat": cat, "stock": stock, "price": price, "vis": vis, "sort": sort},
                  msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""))


def _form_to_product(db: Session, p: Product, form) -> Optional[str]:
    def val(key, default=""):
        v = form.get(key, default)
        return v.strip() if isinstance(v, str) else v

    def num(key):
        raw = val(key)
        if raw in ("", None):
            return None
        try:
            return float(str(raw).replace(",", ""))
        except ValueError:
            return "ERR"

    name = val("name")
    if not name:
        return "Product name is required."
    cat = val("category_id")
    if not cat or not db.get(Category, cat):
        return "Pick a category."
    price, perr = parse_money(val("price"), "Price")
    if perr:
        return perr + " (leave price blank for 'price on request')."
    mrp, merr = parse_money(val("mrp"), "MRP")
    if merr:
        return merr
    sku = val("sku").upper()
    if sku:
        clash = db.query(Product).filter(Product.sku == sku, Product.id != p.id).first()
        if clash:
            return f"SKU {sku} is already used by “{clash.name}”."
    p.name, p.category_id = name, cat
    p.sku = sku or p.sku or unique_sku(db, "GF-" + slugify(name)[:12].upper())
    p.price, p.mrp = price, mrp
    if p.mrp is not None and p.price is not None and p.mrp < p.price:
        p.mrp = p.price
    stock, serr = parse_qty(val("stock"), "Stock")
    if serr:
        return serr
    p.stock = stock
    p.unit = val("unit", "piece") or "piece"
    p.part_family = val("part_family", "spares") or "spares"
    p.group_name = val("group_name")
    p.size, p.pack = val("size"), val("pack")
    p.colours, p.material = val("colours"), val("material")
    p.warranty, p.weight = val("warranty"), val("weight")
    p.description = form.get("description", "").strip()
    p.brand_names = val("brand_names")
    p.image_url = val("image_url")
    rating, rerr = parse_money(val("rating") or 0, "Rating")
    if rerr or (rating is not None and rating > 5):
        return rerr or "Rating must be between 0 and 5."
    reviews, vrerr = parse_qty(val("reviews"), "Reviews")
    if vrerr:
        return vrerr
    sort_order, soerr = parse_qty(val("sort_order") or 1000, "Sort order")
    if soerr:
        return soerr
    p.rating, p.reviews, p.sort_order = (rating or 0), reviews, sort_order
    p.is_new = bool(form.get("is_new"))
    p.is_best = bool(form.get("is_best"))
    p.is_featured = bool(form.get("is_featured"))
    p.visible = bool(form.get("visible"))
    return None


@router.get("/products/new", response_class=HTMLResponse)
def product_new(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    blank = Product(name="", category_id="", stock=0, visible=True, unit="piece",
                    part_family="spares", sort_order=1000)
    return render(request, "product_form.html", db=db, p=blank, new=True,
                  cats=db.query(Category).order_by(Category.name).all(),
                  families=PART_FAMILIES, units=UNITS, err="", msg="")


@router.post("/products/new")
async def product_create(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    form = await request.form()
    p = Product(source="manual")
    problem = _form_to_product(db, p, form)
    if problem:
        return render(request, "product_form.html", db=db, p=p, new=True,
                      cats=db.query(Category).order_by(Category.name).all(),
                      families=PART_FAMILIES, units=UNITS, err=problem, msg="")
    db.add(p)
    db.flush()
    log(db, _user(request), "create", "product", p.id, p.name)
    db.commit()
    return flash(f"/admin/products/{p.id}/edit", msg=f"Created “{p.name}”. Add photos below.")


@router.get("/products/{pid}/edit", response_class=HTMLResponse)
def product_edit(request: Request, pid: int, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    p = db.get(Product, pid)
    if not p:
        return flash("/admin/products", err="That product no longer exists.")
    return render(request, "product_form.html", db=db, p=p, new=False,
                  cats=db.query(Category).order_by(Category.name).all(),
                  families=PART_FAMILIES, units=UNITS,
                  msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""))


@router.post("/products/{pid}/edit")
async def product_update(request: Request, pid: int, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    p = db.get(Product, pid)
    if not p:
        return flash("/admin/products", err="That product no longer exists.")
    form = await request.form()
    problem = _form_to_product(db, p, form)
    if problem:
        return render(request, "product_form.html", db=db, p=p, new=False,
                      cats=db.query(Category).order_by(Category.name).all(),
                      families=PART_FAMILIES, units=UNITS, err=problem, msg="")
    log(db, _user(request), "update", "product", p.id, p.name)
    db.commit()
    return flash(f"/admin/products/{pid}/edit", msg="Saved. The storefront shows it on next load.")


@router.post("/products/{pid}/delete")
def product_delete(request: Request, pid: int, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    p = db.get(Product, pid)
    if not p:
        return flash("/admin/products", err="Already deleted.")
    name = p.name
    for img in list(p.images):
        delete_media(img.url)
    delete_media(p.image_url)
    db.delete(p)
    log(db, _user(request), "delete", "product", pid, name)
    db.commit()
    return flash("/admin/products", msg=f"Deleted “{name}”.")


@router.post("/products/{pid}/duplicate")
def product_duplicate(request: Request, pid: int, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    p = db.get(Product, pid)
    if not p:
        return flash("/admin/products", err="That product no longer exists.")
    clone = Product(**{c.name: getattr(p, c.name) for c in Product.__table__.columns
                       if c.name not in ("id", "sku", "created_at", "updated_at")})
    clone.sku = unique_sku(db, p.sku)
    clone.name = p.name + " (copy)"
    clone.visible = False
    db.add(clone)
    db.flush()
    for img in p.images:
        db.add(ProductImage(product_id=clone.id, url=img.url, sort_order=img.sort_order))
    log(db, _user(request), "duplicate", "product", clone.id, f"from {p.sku}")
    db.commit()
    return flash(f"/admin/products/{clone.id}/edit",
                 msg="Copied. It's hidden until you make it visible.")


@router.post("/products/inline")
async def product_inline(request: Request, db: Session = Depends(get_db)):
    """Inline price / stock / visibility edit from the products table."""
    if not _user(request):
        return JSONResponse({"ok": False, "error": "Signed out — reload and sign in again."}, 401)
    data = await request.json()
    p = db.get(Product, int(data.get("id", 0)))
    if not p:
        return JSONResponse({"ok": False, "error": "Product not found"}, 404)
    field, raw = data.get("field"), data.get("value")
    if field == "price":
        value, err = parse_money(raw, "Price")
        if err:
            return JSONResponse({"ok": False, "error": err}, 400)
        p.price = value
        if p.price is not None and p.mrp is not None and p.mrp < p.price:
            p.mrp = p.price
    elif field == "mrp":
        value, err = parse_money(raw, "MRP")
        if err:
            return JSONResponse({"ok": False, "error": err}, 400)
        p.mrp = value
    elif field == "stock":
        value, err = parse_qty(raw, "Stock")
        if err:
            return JSONResponse({"ok": False, "error": err}, 400)
        p.stock = value
    elif field == "visible":
        p.visible = bool(raw)
    else:
        return JSONResponse({"ok": False, "error": f"Field '{field}' is not editable here"}, 400)
    log(db, _user(request), "inline_edit", "product", p.id, f"{field}={raw}")
    db.commit()
    return {"ok": True, "id": p.id, "field": field,
            "price": p.price, "mrp": p.mrp, "stock": p.stock, "visible": p.visible}


@router.post("/products/bulk")
async def products_bulk(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    form = await request.form()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    action = form.get("action", "")
    back = form.get("back", "/admin/products")
    if not ids:
        return flash(back, err="Tick at least one product first.")
    rows = db.query(Product).filter(Product.id.in_(ids)).all()
    n = len(rows)
    try:
        if action == "show":
            for p in rows:
                p.visible = True
            note = f"{n} product(s) now visible."
        elif action == "hide":
            for p in rows:
                p.visible = False
            note = f"{n} product(s) hidden."
        elif action == "delete":
            for p in rows:
                for img in list(p.images):
                    delete_media(img.url)
                delete_media(p.image_url)
                db.delete(p)
            note = f"Deleted {n} product(s)."
        elif action == "price_pct":
            pct, err = parse_money(form.get("amount") or 0, "Percentage")
            if err or pct is None or pct > 10000:
                return flash(back, err=err or "Use a percentage between 0 and 10000.")
            sign = -1 if (form.get("direction") == "down") else 1
            for p in rows:
                if p.price is not None:
                    p.price = clamp_money(p.price * (1 + sign * pct / 100))
                    if p.mrp is not None and p.mrp < p.price:
                        p.mrp = p.price
            note = f"Adjusted {n} price(s) by {sign * pct}%."
        elif action == "price_flat":
            amt, err = parse_money(form.get("amount") or 0, "Amount")
            if err or amt is None:
                return flash(back, err=err or "The amount needs to be a number.")
            sign = -1 if (form.get("direction") == "down") else 1
            for p in rows:
                if p.price is not None:
                    p.price = clamp_money(p.price + sign * amt)
                    if p.mrp is not None and p.mrp < p.price:
                        p.mrp = p.price
            note = f"Adjusted {n} price(s) by ₹{sign * amt}."
        elif action == "set_stock":
            val, err = parse_qty(form.get("amount") or 0, "Stock")
            if err:
                return flash(back, err=err)
            for p in rows:
                p.stock = val
            note = f"Stock set to {val} on {n} product(s)."
        elif action == "move":
            target = form.get("category_id", "")
            if not db.get(Category, target):
                return flash(back, err="Pick a category to move them into.")
            for p in rows:
                p.category_id = target
            note = f"Moved {n} product(s)."
        elif action == "clear_price":
            for p in rows:
                p.price = None
            note = f"{n} product(s) set to price on request."
        else:
            return flash(back, err="Choose a bulk action.")
    except ValueError:
        return flash(back, err="The amount needs to be a number.")
    log(db, _user(request), f"bulk_{action}", "product", ",".join(map(str, ids))[:60], note)
    db.commit()
    return flash(back, msg=note)


# ------------------------------------------------------------------ images
@router.post("/products/{pid}/images")
async def product_images_upload(request: Request, pid: int,
                                files: list[UploadFile] = File(default=[]),
                                db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    p = db.get(Product, pid)
    if not p:
        return flash("/admin/products", err="That product no longer exists.")
    added, errors = 0, []
    for f in files:
        if not f or not f.filename:
            continue
        try:
            url = save_upload(f.filename, await f.read())
        except ValueError as exc:
            errors.append(f"{f.filename}: {exc}")
            continue
        db.add(ProductImage(product_id=p.id, url=url,
                            sort_order=len(p.images) + added))
        if not p.image_url:
            p.image_url = url
        added += 1
    if added:
        log(db, _user(request), "upload_images", "product", p.id, f"{added} file(s)")
    db.commit()
    msg = f"Uploaded {added} photo(s)." if added else ""
    return flash(f"/admin/products/{pid}/edit", msg=msg, err=" ".join(errors))


@router.post("/products/{pid}/images/{img_id}/delete")
def product_image_delete(request: Request, pid: int, img_id: int, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    img = db.get(ProductImage, img_id)
    p = db.get(Product, pid)
    if img and p:
        if p.image_url == img.url:
            others = [i.url for i in p.images if i.id != img.id]
            p.image_url = others[0] if others else ""
        delete_media(img.url)
        db.delete(img)
        log(db, _user(request), "delete_image", "product", pid, img.url)
        db.commit()
    return flash(f"/admin/products/{pid}/edit", msg="Photo removed.")


@router.post("/products/{pid}/images/{img_id}/primary")
def product_image_primary(request: Request, pid: int, img_id: int, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    img, p = db.get(ProductImage, img_id), db.get(Product, pid)
    if img and p:
        p.image_url = img.url
        log(db, _user(request), "set_primary_image", "product", pid, img.url)
        db.commit()
    return flash(f"/admin/products/{pid}/edit", msg="Main photo updated.")


# -------------------------------------------------------- categories & brands
@router.get("/categories", response_class=HTMLResponse)
def categories(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    counts_map = dict(db.query(Product.category_id, func.count(Product.id))
                      .group_by(Product.category_id).all())
    rows = db.query(Category).order_by(Category.sort_order, Category.name).all()
    return render(request, "categories.html", db=db, rows=rows, counts_map=counts_map,
                  msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""))


@router.post("/categories/save")
async def category_save(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    form = await request.form()
    cid = (form.get("id") or "").strip() or slugify(form.get("name", ""))
    name = (form.get("name") or "").strip()
    if not name:
        return flash("/admin/categories", err="Category name is required.")
    c = db.get(Category, cid) or Category(id=cid)
    c.name = name
    c.code = (form.get("code") or name[:2]).upper()[:8]
    c.description = (form.get("description") or "").strip()
    c.image_url = (form.get("image_url") or "").strip()
    try:
        c.sort_order = int(form.get("sort_order") or 100)
        c.hue = int(form.get("hue") or 210)
    except ValueError:
        return flash("/admin/categories", err="Sort order and hue must be numbers.")
    c.popular = bool(form.get("popular"))
    db.add(c)
    log(db, _user(request), "save", "category", cid, name)
    db.commit()
    return flash("/admin/categories", msg=f"Saved “{name}”.")


@router.post("/categories/{cid}/delete")
def category_delete(request: Request, cid: str, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    used = db.query(Product).filter(Product.category_id == cid).count()
    if used:
        return flash("/admin/categories",
                     err=f"{used} product(s) still use that category — move them first.")
    c = db.get(Category, cid)
    if c:
        db.delete(c)
        log(db, _user(request), "delete", "category", cid, c.name)
        db.commit()
    return flash("/admin/categories", msg="Category deleted.")


@router.get("/brands", response_class=HTMLResponse)
def brands(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    rows = db.query(Brand).order_by(Brand.sort_order, Brand.name).all()
    return render(request, "brands.html", db=db, rows=rows,
                  msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""))


@router.post("/brands/save")
async def brand_save(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return flash("/admin/brands", err="Brand name is required.")
    bid = (form.get("id") or "").strip() or slugify(name)
    b = db.get(Brand, bid) or Brand(id=bid)
    b.name = name
    try:
        b.hue = int(form.get("hue") or 210)
        b.sort_order = int(form.get("sort_order") or 100)
    except ValueError:
        return flash("/admin/brands", err="Hue and sort order must be numbers.")
    db.add(b)
    log(db, _user(request), "save", "brand", bid, name)
    db.commit()
    return flash("/admin/brands", msg=f"Saved “{name}”.")


@router.post("/brands/{bid}/delete")
def brand_delete(request: Request, bid: str, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    b = db.get(Brand, bid)
    if b:
        db.delete(b)
        log(db, _user(request), "delete", "brand", bid, b.name)
        db.commit()
    return flash("/admin/brands", msg="Brand deleted.")


# ----------------------------------------------------------------- import
CSV_FIELDS = ["sku", "name", "category_id", "group_name", "price", "mrp", "stock", "unit",
              "size", "pack", "colours", "material", "warranty", "weight", "brand_names",
              "part_family", "rating", "reviews", "visible", "image_url", "description"]


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    if not _elevated(request):
        return _denied()
    return render(request, "import.html", db=db, fields=CSV_FIELDS,
                  count=db.query(Product).count(),
                  msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""))


@router.get("/export.csv")
def export_csv(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    if not _elevated(request):
        return _denied()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for p in db.query(Product).order_by(Product.category_id, Product.name).all():
        w.writerow({f: csv_safe(getattr(p, f, "")) for f in CSV_FIELDS})
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=gflo-catalogue.csv"})


@router.post("/import")
async def import_csv(request: Request, file: UploadFile = File(...),
                     mode: str = Form("update"), db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    if not _elevated(request):
        return _denied()
    try:
        text = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError:
        return flash("/admin/import", err="Save the file as UTF-8 CSV and try again.")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "sku" not in [f.strip() for f in reader.fieldnames]:
        return flash("/admin/import", err="The CSV needs a 'sku' column. Export first to get the format.")
    updated = created = skipped = 0
    problems = []
    for i, row in enumerate(reader, start=2):
        sku = (row.get("sku") or "").strip()
        if not sku:
            skipped += 1
            continue
        # match exactly first, then case-insensitively, so a spreadsheet that
        # changed the case of a code still updates the right product
        p = (db.query(Product).filter(Product.sku == sku).first()
             or db.query(Product).filter(func.lower(Product.sku) == sku.lower()).first())
        if p is None:
            if mode != "create":
                skipped += 1
                continue
            cat = (row.get("category_id") or "").strip()
            if not db.get(Category, cat):
                problems.append(f"row {i}: unknown category '{cat}'")
                continue
            p = Product(sku=sku.upper(), name=(row.get("name") or sku).strip(),
                        category_id=cat, source="csv")
            db.add(p)
            created += 1
        else:
            updated += 1
        for field in CSV_FIELDS:
            if field not in row or field == "sku":
                continue
            raw = (row.get(field) or "").strip()
            if field in ("price", "mrp"):
                value, err = parse_money(raw, field)
                if err:
                    problems.append(f"row {i}: {err}")
                    continue
                setattr(p, field, value)
            elif field in ("stock", "reviews"):
                value, err = parse_qty(raw, field)
                if err:
                    problems.append(f"row {i}: {err}")
                    continue
                setattr(p, field, value)
            elif field == "rating":
                value, err = parse_money(raw, field)
                if err or (value is not None and value > 5):
                    problems.append(f"row {i}: {err or 'rating must be 0-5'}")
                    continue
                setattr(p, field, value or 0)
            elif field == "visible":
                p.visible = raw.lower() in ("1", "true", "yes", "y")
            elif field == "category_id":
                if raw and db.get(Category, raw):
                    p.category_id = raw
                elif raw:
                    problems.append(f"row {i}: unknown category '{raw}'")
            elif raw or field in ("description", "size", "pack"):
                setattr(p, field, raw)
    log(db, _user(request), "csv_import", "product",
        detail=f"created={created} updated={updated} skipped={skipped}")
    db.commit()
    msg = f"Import done — {updated} updated, {created} created, {skipped} skipped."
    return flash("/admin/import", msg=msg,
                 err=("; ".join(problems[:6]) + (" …" if len(problems) > 6 else "")) if problems else "")


# ---------------------------------------------------------------- settings
@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    if not _elevated(request):
        return _denied()
    values = {k: get_setting(db, k) for k in DEFAULT_SETTINGS}
    users = db.query(AdminUser).order_by(AdminUser.id).all()
    return render(request, "settings.html", db=db, values=values, users=users,
                  msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""))


@router.post("/settings")
async def settings_save(request: Request, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    if not _elevated(request):
        return _denied()
    form = await request.form()
    for key in DEFAULT_SETTINGS:
        if key == "show_prices":
            set_setting(db, key, "true" if form.get("show_prices") else "false")
        elif key in form:
            set_setting(db, key, (form.get(key) or "").strip())
    log(db, _user(request), "update", "settings", detail="store settings")
    db.commit()
    return flash("/admin/settings", msg="Settings saved.")


@router.post("/settings/password")
async def change_password(request: Request, current: str = Form(...), new: str = Form(...),
                          confirm: str = Form(...), db: Session = Depends(get_db)):
    username = _user(request)
    if not username:
        return _login_redirect(request)
    user = db.query(AdminUser).filter(AdminUser.username == username).first()
    if not user or not sec.verify_password(current, user.password_hash):
        return flash("/admin/settings", err="Current password is wrong.")
    if new != confirm:
        return flash("/admin/settings", err="The two new passwords don't match.")
    problem = sec.password_problem(new)
    if problem:
        return flash("/admin/settings", err=problem)
    user.password_hash = sec.hash_password(new)
    # invalidate every session minted with the old password, including any
    # cookie an attacker may already hold
    user.token_version = (user.token_version or 0) + 1
    log(db, username, "change_password", "admin_user", user.id)
    db.commit()
    resp = flash("/admin/settings", msg="Password changed. Other sessions were signed out.")
    resp.set_cookie(sec.SESSION_COOKIE,
                    sec.make_session(user.username, user.token_version),
                    **sec.cookie_kwargs(request))
    return resp


@router.post("/settings/users")
async def add_user(request: Request, username: str = Form(...), name: str = Form(""),
                   password: str = Form(...), role: str = Form("editor"),
                   db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    if not _elevated(request):
        return _denied()
    uname = username.strip().lower()
    if not uname:
        return flash("/admin/settings", err="Username is required.")
    if db.query(AdminUser).filter(func.lower(AdminUser.username) == uname).first():
        return flash("/admin/settings", err="That username already exists.")
    problem = sec.password_problem(password)
    if problem:
        return flash("/admin/settings", err=problem)
    db.add(AdminUser(username=uname, name=name.strip(),
                     role=("admin" if role == "admin" else "editor"),
                     token_version=0,
                     password_hash=sec.hash_password(password)))
    log(db, _user(request), "create", "admin_user", uname)
    db.commit()
    return flash("/admin/settings", msg=f"Admin user “{uname}” added.")


@router.post("/settings/users/{uid}/delete")
def delete_user(request: Request, uid: int, db: Session = Depends(get_db)):
    me = _user(request)
    if not me:
        return _login_redirect(request)
    if not _elevated(request):
        return _denied()
    user = db.get(AdminUser, uid)
    if not user:
        return flash("/admin/settings", err="No such user.")
    if user.is_owner:
        return flash("/admin/settings", err="The owner account can't be removed.")
    if user.username == me:
        return flash("/admin/settings", err="You can't delete the account you're signed in with.")
    db.delete(user)
    log(db, me, "delete", "admin_user", uid, user.username)
    db.commit()
    return flash("/admin/settings", msg="Admin user removed.")


@router.get("/activity", response_class=HTMLResponse)
def activity(request: Request, page: int = 1, db: Session = Depends(get_db)):
    if not _user(request):
        return _login_redirect(request)
    per = 100
    total = db.query(AuditLog).count()
    rows = (db.query(AuditLog).order_by(AuditLog.ts.desc())
            .offset((max(1, page) - 1) * per).limit(per).all())
    return render(request, "activity.html", db=db, rows=rows, page=page,
                  pages=max(1, math.ceil(total / per)), total=total)
