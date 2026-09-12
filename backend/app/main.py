"""G-FLO storefront + admin backend.

Serves:
  /                the storefront (site/gflo.html)
  /api/*           read-only catalogue JSON the storefront loads at boot
  /admin/*         password-protected admin website (no Firebase, no OAuth)
  /media/*         uploaded product photos
"""
import os
import re
from fastapi import FastAPI, Request
from fastapi.responses import (FileResponse, HTMLResponse, RedirectResponse,
                               JSONResponse, PlainTextResponse, Response)
from fastapi.staticfiles import StaticFiles

from . import monitoring
monitoring.init()          # no-op unless SENTRY_DSN is set

from .db import Base, engine, SessionLocal, MEDIA_DIR, ensure_schema
from . import models  # noqa: F401  (registers tables)
from . import security as sec
from .api import router as api_router
from .admin import router as admin_router
from .store import ensure_defaults
from . import seo

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # backend/
SITE_DIR = os.environ.get("SITE_DIR", os.path.join(os.path.dirname(BASE_DIR), "site"))

# The interactive API docs enumerate every route (including the admin surface),
# so they stay off unless explicitly enabled for development.
ENABLE_DOCS = os.environ.get("ENABLE_DOCS", "").lower() in ("1", "true", "yes")

# Optional dedicated hostname for the admin console, e.g. ADMIN_HOST=admin.gflo.in
# Unset -> nothing changes and the console stays at /admin on every hostname.
# Set   -> the console answers on that hostname only, and /admin is refused on
#          the storefront hostname. The session cookie is then scoped to the
#          admin hostname, so it never travels with shop or API requests.
ADMIN_HOST = (os.environ.get("ADMIN_HOST") or "").split(":")[0].strip().lower()

# Optional split of the storefront across two hostnames, GM-Modular style:
#   SITE_HOST=gflo.in         the brand site  (home, guides, about, contact, policies)
#   STORE_HOST=store.gflo.in  the shop        (catalogue, product pages, cart, account)
# Both are served by THIS app; the storefront JS reads the pair below and sends
# a visitor to the right hostname for the page they asked for. Leave either one
# unset and the site behaves as a single host exactly as before.
SITE_HOST = (os.environ.get("SITE_HOST") or "").split(":")[0].strip().lower()
STORE_HOST = (os.environ.get("STORE_HOST") or "").split(":")[0].strip().lower()

app = FastAPI(title="G-FLO Store",
              docs_url="/api/docs" if ENABLE_DOCS else None,
              openapi_url="/openapi.json" if ENABLE_DOCS else None,
              redoc_url=None)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    try:
        ensure_schema()
    except Exception as exc:                          # pragma: no cover
        print(f"[gflo] migration skipped: {exc}")
    db = SessionLocal()
    try:
        ensure_defaults(db)
        from .models import AdminUser
        if db.query(AdminUser).count() == 0:
            user = os.environ.get("ADMIN_USERNAME", "admin")
            pwd = os.environ.get("ADMIN_PASSWORD")
            if pwd:
                db.add(AdminUser(username=user.lower(), name="Owner", is_owner=True,
                                 role="owner", token_version=0,
                                 password_hash=sec.hash_password(pwd)))
                db.commit()
                print(f"[gflo] created admin user '{user}' from ADMIN_PASSWORD")
            else:
                print("[gflo] no admin user yet — run scripts/create_admin.py "
                      "or set ADMIN_USERNAME / ADMIN_PASSWORD and restart")
    finally:
        db.close()


# --------------------------------------------------------------- middleware
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Host routing + CSRF enforcement for /admin writes + security headers."""
    path = request.url.path

    if ADMIN_HOST:
        # rstrip("."): "admin.gflo.in." is a legitimate fully-qualified form of
        # the same hostname, and without this it missed the ADMIN_HOST match and
        # took an extra redirect through the public host.
        host = (request.headers.get("host") or "").split(":")[0].strip().lower().rstrip(".")
        if host == ADMIN_HOST:
            # admin.example.com/  ->  the console. Assets and the public API are
            # left alone so the console's own CSS/JS/photos still resolve.
            if not path.startswith(("/admin", "/static/", "/media/", "/api/", "/healthz")):
                request.scope["path"] = path = "/admin" + ("" if path == "/" else path)
        elif path == "/admin" or path.startswith("/admin/"):
            # keep the console off the shop hostname entirely
            return RedirectResponse(f"https://{ADMIN_HOST}{path}", status_code=301)
    if request.method not in SAFE_METHODS and path.startswith("/admin"):
        submitted = request.headers.get(sec.CSRF_HEADER)
        if not submitted:
            ctype = (request.headers.get("content-type") or "").split(";")[0].strip()
            if ctype in ("application/x-www-form-urlencoded", "multipart/form-data"):
                try:
                    # body() first: it caches the payload so Starlette's
                    # _CachedRequest can replay it to the route handler.
                    # form() alone consumes the stream and the endpoint would
                    # then see an empty body (422).
                    await request.body()
                    form = await request.form()
                    submitted = form.get(sec.CSRF_FIELD)
                except Exception:
                    submitted = None
        if not sec.csrf_ok(request, submitted):
            wants_json = "application/json" in (request.headers.get("accept") or "") \
                or (request.headers.get("content-type") or "").startswith("application/json")
            body = {"ok": False, "error": "Your session expired or this form went stale. Reload and try again."}
            return JSONResponse(body, 403) if wants_json else HTMLResponse(
                "<h1>403 — request rejected</h1><p>Your sign-in form went stale. "
                "<a href=\"/admin\">Reload the console</a> and try again.</p>", 403)

    # Mint the CSRF token BEFORE the handler runs so templates rendered on this
    # same response can embed it (the cookie isn't readable back off the request).
    fresh_csrf = None
    if path.startswith("/admin") and request.method in SAFE_METHODS \
            and not request.cookies.get(sec.CSRF_COOKIE):
        fresh_csrf = sec.new_csrf_token()
    request.state.csrf = fresh_csrf or request.cookies.get(sec.CSRF_COOKIE, "")

    if path.startswith("/admin") and monitoring.configured():
        try:
            monitoring.note_admin(sec.current_user(request))
        except Exception:
            pass

    response = await call_next(request)

    # hand out a CSRF token so forms and the inline-edit fetch can echo it back
    if fresh_csrf:
        response.set_cookie(sec.CSRF_COOKIE, fresh_csrf, **sec.csrf_cookie_kwargs(request))

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if path.startswith("/admin"):
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        response.headers.setdefault("Cache-Control", "no-store")
    if path.startswith("/media"):
        response.headers.setdefault("Content-Disposition", "inline")
    if sec.COOKIE_SECURE_ALWAYS or request.headers.get("x-forwarded-proto") == "https" \
            or request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(api_router)
app.include_router(admin_router)

app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


_SITE_CACHE = {"mtime": None, "html": None}


def _storefront_html(index: str) -> str:
    """gflo.html with the hostname pair injected, re-read when the file changes."""
    mtime = os.stat(index).st_mtime
    if _SITE_CACHE["mtime"] != mtime:
        html = open(index, encoding="utf-8").read()
        if SITE_HOST and STORE_HOST:
            import json as _json
            cfg = ("<script>window.GFLO_HOSTS=" +
                   _json.dumps({"main": SITE_HOST, "store": STORE_HOST}) + ";</script>")
            if "</head>" in html:
                html = html.replace("</head>", cfg + "</head>", 1)
        _SITE_CACHE.update(mtime=mtime, html=html)
    return _SITE_CACHE["html"]


_STATIC_META_RE = re.compile(
    r"<title>.*?</title>"
    r"|<meta\s+name=\"description\"[^>]*>"
    r"|<meta\s+property=\"og:(?:title|description|type|image|url|site_name)\"[^>]*>"
    r"|<meta\s+name=\"twitter:[^\"]*\"[^>]*>"
    r"|<link\s+rel=\"canonical\"[^>]*>",
    re.I | re.S)


def _strip_static_meta(html: str) -> str:
    """Remove gflo.html's one fixed set of tags before injecting the real ones.

    Without this the page carries two titles and two og:title tags, and which
    one a crawler believes is anyone's guess.
    """
    head_end = html.lower().find("</head>")
    if head_end == -1:
        return html
    return _STATIC_META_RE.sub("", html[:head_end]) + html[head_end:]


def _canonical_host(request: Request) -> str:
    """The hostname to build canonical / sitemap URLs from.

    When SITE_HOST / STORE_HOST are configured this NEVER trusts the request's
    Host header: an attacker who could set it would otherwise poison your
    canonical tags and point your search ranking at their own domain. An
    unrecognised Host falls back to the configured hostname.

    With neither configured there is nothing better to use, so the request's
    host is used — a sitemap needs absolute URLs to be valid at all, and a
    relative <loc> is rejected outright. Configure SITE_HOST on any real
    deployment so this path is never taken.
    """
    asked = (request.headers.get("host") or "").split(":")[0].strip().lower().rstrip(".")
    if SITE_HOST or STORE_HOST:
        if asked in (h for h in (SITE_HOST, STORE_HOST) if h):
            return asked
        return SITE_HOST or STORE_HOST
    return asked


def _render_storefront(request: Request, path: str = "/"):
    """gflo.html with this route's real meta tags injected into <head>.

    The SPA used to serve one fixed set of tags for every route, so a crawler
    or a WhatsApp preview saw the same generic home page whatever was shared.
    """
    index = os.path.join(SITE_DIR, "gflo.html")
    if not os.path.exists(index):
        return HTMLResponse(
            "<h1>Storefront file missing</h1>"
            f"<p>Expected <code>{index}</code>. Set SITE_DIR to the folder holding gflo.html.</p>", 500)
    html = _storefront_html(index)
    status = 200
    try:
        db = SessionLocal()
        try:
            # with no host configured, fall back to the requested host so the
            # canonical URL is still absolute
            _site = SITE_HOST or (("" if STORE_HOST else _canonical_host(request)))
            meta = seo.page_meta(path, db, _site, STORE_HOST)
        finally:
            db.close()
        html = _strip_static_meta(html).replace("</head>", seo.meta_html(meta) + "</head>", 1)
        # a URL that names no real product/category/brand is genuinely absent
        if meta["page"] == "notfound":
            status = 404
    except Exception:
        pass                      # never let meta generation take the shop down
    return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def storefront(request: Request):
    return _render_storefront(request, "/")


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request):
    return PlainTextResponse(seo.build_robots(_canonical_host(request)),
                             headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap.xml")
def sitemap(request: Request):
    host = _canonical_host(request)
    db = SessionLocal()
    try:
        xml = seo.build_sitemap(db, host, SITE_HOST, STORE_HOST)
    finally:
        db.close()
    return Response(content=xml, media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=900"})


@app.get("/gflo.html")
def storefront_alias():
    return RedirectResponse("/", status_code=301)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# --------------------------------------------------------- sentry self-test
# Sentry's onboarding "Verify" step: this route crashes on purpose so Sentry
# records its first event and finishes setup.
#
# It only exists when SENTRY_DEBUG_ROUTE is switched on, so the live shop never
# carries a public URL that anyone can hit to generate 500s and burn through
# your Sentry quota. Turn it on, visit it once, then turn it off again — no
# code change needed either way.
if os.environ.get("SENTRY_DEBUG_ROUTE", "").lower() in ("1", "true", "yes"):
    @app.get("/sentry-debug")
    def sentry_debug():
        division_by_zero = 1 / 0          # noqa: F841  (intentional)
        return {"ok": True}


@app.get("/{asset_path:path}")
def site_assets(request: Request, asset_path: str):
    """Storefront files first, then the app itself for clean URLs.

    Now that routes are real paths rather than "#/..." fragments, /categories
    and /p/GF-CS-SPA-0101 arrive here on a hard refresh or a shared link. They
    are not files, so they used to 404. They now render the app, which reads
    the path and draws the right page.
    """
    # API and admin keep answering in their own language; handing a JSON client
    # a page of HTML instead of a 404 is worse than the 404.
    if asset_path.startswith(("api/", "admin/", "media/", "static/")):
        return JSONResponse({"detail": "Not found"}, 404)

    safe = os.path.normpath(asset_path).lstrip("./")
    full = os.path.join(SITE_DIR, safe)
    if os.path.commonpath([os.path.abspath(full), os.path.abspath(SITE_DIR)]) != os.path.abspath(SITE_DIR):
        return JSONResponse({"detail": "Not found"}, 404)
    if os.path.isfile(full):
        return FileResponse(full, headers={"Cache-Control": "public, max-age=86400"})

    # A path with a file extension was asking for a file that is not there —
    # a missing image should stay a 404 and not silently become a web page.
    last = safe.rsplit("/", 1)[-1]
    if "." in last and not last.startswith("."):
        return JSONResponse({"detail": "Not found"}, 404)

    return _render_storefront(request, "/" + asset_path.strip("/"))
