"""Festival posters & promo banners.

Admin (owner/admin accounts only):
  GET  /admin/posters                 list + "add a poster" form
  POST /admin/posters/new             upload a new poster
  GET  /admin/posters/{id}/edit       edit form
  POST /admin/posters/{id}/edit       save changes (images optional = keep)
  POST /admin/posters/{id}/toggle     switch one poster on / off
  POST /admin/posters/master          MASTER switch: all posters on / off
  POST /admin/posters/{id}/delete     delete poster and its uploaded images

Public:
  GET  /api/posters                   what the storefront should show right now

Uploads go through store.save_upload, so posters get exactly the same checks as
product photos (must really decode as an image, 8 MB cap, decompression-bomb
guard) and land in the persistent media folder. CSRF on every POST is enforced
by the middleware in main.py, as for the rest of /admin.
"""
import datetime as dt
import os
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from .db import get_db
from .models import Poster
from .store import log, save_upload, delete_media, get_setting, set_setting
from .admin import render, flash, _user, _elevated, _denied, _login_redirect

admin_router = APIRouter(prefix="/admin/posters", tags=["admin"])
public_router = APIRouter(prefix="/api", tags=["public"])

STYLES = {"popup": "Popup when the site opens", "strip": "Banner strip above the header"}
FREQUENCIES = {"daily": "Once a day per visitor", "once": "Only once per visitor",
               "visit": "Every new visit"}

# Dates are entered as shop-local calendar days. IST (+5:30) unless overridden,
# e.g. STORE_UTC_OFFSET_MINUTES=240 for Dubai. A fixed offset avoids needing
# tzdata inside the slim Docker image.
_OFFSET = dt.timedelta(minutes=int(os.environ.get("STORE_UTC_OFFSET_MINUTES", "330") or 330))


def shop_today() -> dt.date:
    return (dt.datetime.utcnow() + _OFFSET).date()


def poster_status(p: Poster, today: Optional[dt.date] = None) -> str:
    today = today or shop_today()
    if not p.active:
        return "off"
    if p.starts_on and today < p.starts_on:
        return "scheduled"
    if p.ends_on and today > p.ends_on:
        return "expired"
    return "live"


MASTER_KEY = "posters_enabled"


def posters_enabled(db: Session) -> bool:
    """The owner's master switch. ON by default, so deploying changes nothing."""
    return get_setting(db, MASTER_KEY, "true") != "false"


def live_posters(db: Session) -> dict:
    """The poster the storefront shows for each style: newest live one wins.
    Nothing at all while the master switch is OFF."""
    if not posters_enabled(db):
        return {}
    today = shop_today()
    rows = (db.query(Poster).filter(Poster.active == True)              # noqa: E712
            .order_by(Poster.updated_at.desc(), Poster.id.desc()).all())
    chosen = {}
    for p in rows:
        if p.style in STYLES and p.style not in chosen and poster_status(p, today) == "live":
            chosen[p.style] = p
    return chosen


# ------------------------------------------------------------- validation
def _clean_link(raw: str) -> tuple[str, str]:
    """(value, error). Only same-site paths and http(s)/tel/mailto/WhatsApp
    links are allowed — a javascript: URL here would run on every visitor."""
    v = (raw or "").strip()
    if not v:
        return "", ""
    if len(v) > 500 or any(ch in v for ch in "\r\n\t<>\"'` ") or "\\" in v:
        return "", "That link isn't valid. Use a page on this site like /categories or a full https:// address."
    low = v.lower()
    if v.startswith("/") and not v.startswith("//"):
        return v, ""
    if low.startswith(("https://", "http://", "tel:", "mailto:")):
        return v, ""
    return "", "Links must start with / (a page on this site), https://, tel: or mailto:."


def _parse_date(raw: str, label: str) -> tuple[Optional[dt.date], str]:
    v = (raw or "").strip()
    if not v:
        return None, ""
    try:
        return dt.date.fromisoformat(v), ""
    except ValueError:
        return None, f"{label} isn't a valid date."


def _has_file(f) -> bool:
    return f is not None and hasattr(f, "filename") and bool(getattr(f, "filename", ""))


async def _store_file(f) -> tuple[str, str]:
    """(url, error) for an uploaded poster image."""
    try:
        return save_upload(f.filename, await f.read()), ""
    except ValueError as exc:
        return "", f"{f.filename}: {exc}"


async def _apply_form(p: Poster, form) -> str:
    """Copy the text fields of the form onto p. Returns an error or ""."""
    title = (form.get("title") or "").strip()[:120]
    if not title:
        return "Give the poster a name, e.g. “Diwali Sale 2026”."
    link, err = _clean_link(form.get("link_url") or "")
    if err:
        return err
    starts, err = _parse_date(form.get("starts_on"), "Start date")
    if err:
        return err
    ends, err = _parse_date(form.get("ends_on"), "End date")
    if err:
        return err
    if starts and ends and ends < starts:
        return "The end date is before the start date."
    style = form.get("style") or "popup"
    freq = form.get("frequency") or "daily"
    p.title = title
    p.link_url = link
    p.button_text = (form.get("button_text") or "").strip()[:40]
    p.style = style if style in STYLES else "popup"
    p.frequency = freq if freq in FREQUENCIES else "daily"
    p.starts_on, p.ends_on = starts, ends
    p.active = bool(form.get("active"))
    return ""


def _gate(request: Request):
    """None if the caller may manage posters, else the response to return."""
    if not _user(request):
        return _login_redirect(request)
    if not _elevated(request):
        return _denied()
    return None


def _page(request: Request, db: Session, edit: Optional[Poster] = None):
    today = shop_today()
    rows = db.query(Poster).order_by(Poster.updated_at.desc(), Poster.id.desc()).all()
    showing = {p.id for p in live_posters(db).values()}
    return render(request, "posters.html", db=db, rows=rows, edit=edit,
                  status={p.id: poster_status(p, today) for p in rows},
                  showing=showing, styles=STYLES, frequencies=FREQUENCIES,
                  today=today.isoformat(), master_on=posters_enabled(db),
                  msg=request.query_params.get("msg", ""),
                  err=request.query_params.get("err", ""))


# ------------------------------------------------------------------ admin
@admin_router.get("", response_class=HTMLResponse)
@admin_router.get("/", response_class=HTMLResponse)
def posters_list(request: Request, db: Session = Depends(get_db)):
    stop = _gate(request)
    return stop or _page(request, db)


@admin_router.post("/new")
async def poster_create(request: Request, db: Session = Depends(get_db)):
    stop = _gate(request)
    if stop:
        return stop
    form = await request.form()
    p = Poster()
    err = await _apply_form(p, form)
    if err:
        return flash("/admin/posters", err=err)
    if not _has_file(form.get("image")):
        return flash("/admin/posters", err="Choose the poster image to upload.")
    url, err = await _store_file(form.get("image"))
    if err:
        return flash("/admin/posters", err=err)
    p.image_url = url
    if _has_file(form.get("mobile_image")):
        murl, err = await _store_file(form.get("mobile_image"))
        if err:
            delete_media(url)
            return flash("/admin/posters", err=err)
        p.mobile_image_url = murl
    db.add(p)
    db.flush()
    log(db, _user(request), "create", "poster", p.id, p.title)
    db.commit()
    state = poster_status(p)
    note = {"live": "It's live on the storefront now.",
            "scheduled": f"It will go live on {p.starts_on:%d %b %Y}." if p.starts_on else "",
            "off": "It's switched off — turn it on when you're ready.",
            "expired": "Its end date has already passed, so it won't show."}.get(state, "")
    return flash("/admin/posters", msg=f"Poster “{p.title}” uploaded. {note}".strip())


@admin_router.get("/{pid}/edit", response_class=HTMLResponse)
def poster_edit(request: Request, pid: int, db: Session = Depends(get_db)):
    stop = _gate(request)
    if stop:
        return stop
    p = db.get(Poster, pid)
    if not p:
        return flash("/admin/posters", err="That poster no longer exists.")
    return _page(request, db, edit=p)


@admin_router.post("/{pid}/edit")
async def poster_update(request: Request, pid: int, db: Session = Depends(get_db)):
    stop = _gate(request)
    if stop:
        return stop
    p = db.get(Poster, pid)
    if not p:
        return flash("/admin/posters", err="That poster no longer exists.")
    back = f"/admin/posters/{pid}/edit"
    form = await request.form()
    err = await _apply_form(p, form)
    if err:
        db.rollback()
        return flash(back, err=err)
    new_main = new_mobile = ""
    if _has_file(form.get("image")):
        new_main, err = await _store_file(form.get("image"))
        if err:
            db.rollback()
            return flash(back, err=err)
    if _has_file(form.get("mobile_image")):
        new_mobile, err = await _store_file(form.get("mobile_image"))
        if err:
            delete_media(new_main)
            db.rollback()
            return flash(back, err=err)
    if new_main:
        delete_media(p.image_url)
        p.image_url = new_main
    if new_mobile:
        delete_media(p.mobile_image_url)
        p.mobile_image_url = new_mobile
    elif form.get("remove_mobile") and p.mobile_image_url:
        delete_media(p.mobile_image_url)
        p.mobile_image_url = ""
    p.updated_at = dt.datetime.utcnow()
    log(db, _user(request), "update", "poster", p.id, p.title)
    db.commit()
    return flash("/admin/posters", msg=f"Saved “{p.title}”.")


@admin_router.post("/master")
async def posters_master(request: Request, db: Session = Depends(get_db)):
    """Owner's master ON / OFF for every poster and banner on the website."""
    stop = _gate(request)
    if stop:
        return stop
    form = await request.form()
    turn_on = (form.get("state") or "") == "on"
    set_setting(db, MASTER_KEY, "true" if turn_on else "false")
    log(db, _user(request), "enable" if turn_on else "disable", "poster", "ALL",
        "master switch " + ("ON" if turn_on else "OFF"))
    db.commit()
    return flash("/admin/posters",
                 msg=("Posters are ON — live posters show on the website again "
                      "(within about a minute)." if turn_on else
                      "Posters are OFF — nothing shows on the website now (within about a "
                      "minute). Your posters are kept; switch back ON any time."))


@admin_router.post("/{pid}/toggle")
def poster_toggle(request: Request, pid: int, db: Session = Depends(get_db)):
    stop = _gate(request)
    if stop:
        return stop
    p = db.get(Poster, pid)
    if not p:
        return flash("/admin/posters", err="That poster no longer exists.")
    p.active = not p.active
    p.updated_at = dt.datetime.utcnow()
    log(db, _user(request), "enable" if p.active else "disable", "poster", p.id, p.title)
    db.commit()
    return flash("/admin/posters",
                 msg=f"“{p.title}” switched {'on' if p.active else 'off'}.")


@admin_router.post("/{pid}/delete")
def poster_delete(request: Request, pid: int, db: Session = Depends(get_db)):
    stop = _gate(request)
    if stop:
        return stop
    p = db.get(Poster, pid)
    if not p:
        return flash("/admin/posters", err="That poster no longer exists.")
    delete_media(p.image_url)
    delete_media(p.mobile_image_url)
    title = p.title
    db.delete(p)
    log(db, _user(request), "delete", "poster", pid, title)
    db.commit()
    return flash("/admin/posters", msg=f"Deleted “{title}”.")


# ----------------------------------------------------------------- public
def poster_json(p: Poster) -> dict:
    return {
        "id": p.id,
        # changes whenever the poster is edited, so an edited poster is shown
        # again even to visitors who closed the previous version
        "v": f"{p.id}-{int((p.updated_at or p.created_at or dt.datetime.utcnow()).timestamp() * 1000)}",
        "title": p.title,
        "img": p.image_url,
        "imgMobile": p.mobile_image_url or "",
        "link": p.link_url or "",
        "cta": p.button_text or "",
        "freq": p.frequency or "daily",
    }


@public_router.get("/posters")
def posters_public(db: Session = Depends(get_db)):
    chosen = live_posters(db)
    body = {"popup": poster_json(chosen["popup"]) if "popup" in chosen else None,
            "strip": poster_json(chosen["strip"]) if "strip" in chosen else None}
    return JSONResponse(body, headers={"Cache-Control": "public, max-age=60"})
