"""G-FLO storefront + admin backend.

Serves:
  /                the storefront (site/gflo.html)
  /api/*           read-only catalogue JSON the storefront loads at boot
  /admin/*         password-protected admin website (no Firebase, no OAuth)
  /media/*         uploaded product photos
"""
import os
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import monitoring
monitoring.init()          # no-op unless SENTRY_DSN is set

from .db import Base, engine, SessionLocal, MEDIA_DIR, ensure_schema
from . import models  # noqa: F401  (registers tables)
from . import security as sec
from .api import router as api_router
from .admin import router as admin_router
from .store import ensure_defaults

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # backend/
SITE_DIR = os.environ.get("SITE_DIR", os.path.join(os.path.dirname(BASE_DIR), "site"))

# The interactive API docs enumerate every route (including the admin surface),
# so they stay off unless explicitly enabled for development.
ENABLE_DOCS = os.environ.get("ENABLE_DOCS", "").lower() in ("1", "true", "yes")

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
    """CSRF enforcement for /admin writes + security headers on every response."""
    path = request.url.path
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


@app.get("/", response_class=HTMLResponse)
def storefront():
    index = os.path.join(SITE_DIR, "gflo.html")
    if not os.path.exists(index):
        return HTMLResponse(
            "<h1>Storefront file missing</h1>"
            f"<p>Expected <code>{index}</code>. Set SITE_DIR to the folder holding gflo.html.</p>", 500)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


@app.get("/gflo.html")
def storefront_alias():
    return RedirectResponse("/", status_code=301)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/{asset_path:path}")
def site_assets(asset_path: str):
    """Serve the storefront's own folders (brand-photos/, tools-photos/, assets/)."""
    if asset_path.startswith(("api/", "admin/", "media/", "static/")):
        return JSONResponse({"detail": "Not found"}, 404)
    safe = os.path.normpath(asset_path).lstrip("./")
    full = os.path.join(SITE_DIR, safe)
    if os.path.commonpath([os.path.abspath(full), os.path.abspath(SITE_DIR)]) != os.path.abspath(SITE_DIR):
        return JSONResponse({"detail": "Not found"}, 404)
    if os.path.isfile(full):
        return FileResponse(full, headers={"Cache-Control": "public, max-age=86400"})
    return JSONResponse({"detail": "Not found"}, 404)

@app.get("/sentry-debug")
async def trigger_error():
    division_by_zero = 1 / 0