"""Settings helpers, audit log, SKU + image utilities."""
import os, re, secrets
from typing import Optional
from sqlalchemy.orm import Session

from .db import MEDIA_DIR
from .models import Setting, AuditLog, Product

DEFAULT_SETTINGS = {
    "store_name": "G-FLO",
    "contact_email": "sales@gflo.in",
    "contact_phone": "+91 77421 02402",
    "contact_phone2": "+91 63671 75266",
    "contact_address": "Raj Electricals, Ahmedabad, Gujarat",
    "whatsapp": "+91 77421 02402",
    "show_prices": "true",
    "price_note": "Rates are ex-Ahmedabad and exclusive of GST unless stated. Prices subject to change.",
    "por_label": "Price on request",
}

# exposed to the storefront through /api/catalog
PUBLIC_SETTING_KEYS = ["store_name", "contact_email", "contact_phone", "contact_phone2",
                       "contact_address", "whatsapp", "price_note", "por_label"]


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    if row is None:
        return DEFAULT_SETTINGS.get(key, default)
    return row.value


def set_setting(db: Session, key: str, value: str):
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def ensure_defaults(db: Session):
    for key, value in DEFAULT_SETTINGS.items():
        if db.get(Setting, key) is None:
            db.add(Setting(key=key, value=value))
    db.commit()


def log(db: Session, username: str, action: str, entity: str, entity_id="", detail=""):
    db.add(AuditLog(username=username, action=action, entity=entity,
                    entity_id=str(entity_id), detail=detail[:2000]))


def slugify(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or fallback


def unique_sku(db: Session, base: str) -> str:
    base = re.sub(r"[^A-Z0-9\-]", "", (base or "GF-NEW").upper()).strip("-") or "GF-NEW"
    sku, n = base, 1
    while db.query(Product).filter(Product.sku == sku).first():
        n += 1
        sku = f"{base}-{n}"
    return sku


ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
# Reject canvases that would blow up in memory when decoded (a 9000x9000 PNG is
# ~240 MB of pixels from a 250 KB file). 40 MP is far above any product photo.
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_SIDE = 12_000
# Pillow's own guard, so a malicious file trips it during decode as well.
_PIL_PIXEL_LIMIT = MAX_IMAGE_PIXELS


def save_upload(filename: str, data: bytes) -> str:
    """Store an uploaded photo under MEDIA_DIR/products and return its public URL.

    The bytes must actually decode as one of the allowed image formats: checking
    only the extension let arbitrary content (HTML/scripts) be stored and served
    from the media path, and gave no protection against decompression bombs.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise ValueError(f"Unsupported image type '{ext or filename}'. Use JPG, PNG, WebP, GIF or AVIF.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image is larger than 8 MB.")
    if not data:
        raise ValueError("Empty file.")

    fmt, size = _probe_image(data)
    if fmt is None:
        raise ValueError("That file isn't a readable image.")
    if size[0] * size[1] > MAX_IMAGE_PIXELS or max(size) > MAX_IMAGE_SIDE:
        raise ValueError(f"Image is too large to process ({size[0]}x{size[1]} pixels).")
    # trust the decoded format over the supplied extension
    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "AVIF": ".avif"}.get(fmt, ext)

    name = f"{secrets.token_hex(8)}{ext}"
    path = os.path.join(MEDIA_DIR, "products", name)
    with open(path, "wb") as fh:
        fh.write(data)
    # keep the served file reasonably sized
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = _PIL_PIXEL_LIMIT
        with Image.open(path) as im:
            if max(im.size) > 1600:
                im.thumbnail((1600, 1600))
                im.save(path)
    except Exception:
        pass
    return f"/media/products/{name}"


def _probe_image(data: bytes):
    """(format, (w, h)) if the bytes decode as an image, else (None, (0, 0))."""
    import io
    try:
        from PIL import Image
    except Exception:                      # Pillow unavailable — fall back to magic bytes
        return (_magic_format(data), (1, 1))
    Image.MAX_IMAGE_PIXELS = _PIL_PIXEL_LIMIT
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()                    # structural check, raises on junk
        with Image.open(io.BytesIO(data)) as im:
            return (im.format, im.size)    # size read from the header only
    except Exception:
        return (None, (0, 0))


def _magic_format(data: bytes):
    sigs = [(b"\xff\xd8\xff", "JPEG"), (b"\x89PNG\r\n\x1a\n", "PNG"),
            (b"GIF87a", "GIF"), (b"GIF89a", "GIF")]
    for sig, fmt in sigs:
        if data.startswith(sig):
            return fmt
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "AVIF"
    return None


# ----------------------------------------------------------- value validation
# Prices/quantities are bounded and must be finite: an unbounded float() let a
# bulk "+1e308%" write inf into the DB, after which /api/catalog could no longer
# be serialised and returned HTTP 500 to every storefront visitor.
MAX_MONEY = 1_000_000_000.0
MAX_QTY = 1_000_000_000


def parse_money(raw, field: str = "Price"):
    """-> (value_or_None, error_or_None). Blank means 'price on request'."""
    import math
    if raw is None:
        return None, None
    text = str(raw).strip().replace(",", "")
    if text == "":
        return None, None
    try:
        val = float(text)
    except (TypeError, ValueError):
        return None, f"{field} must be a number."
    if not math.isfinite(val):
        return None, f"{field} must be a finite number."
    if val < 0:
        return None, f"{field} can't be negative."
    if val > MAX_MONEY:
        return None, f"{field} can't be more than {MAX_MONEY:,.0f}."
    return round(val, 2), None


def clamp_money(val):
    """Keep a computed price finite and in range (used after bulk arithmetic)."""
    import math
    if val is None:
        return None
    if not math.isfinite(val):
        return MAX_MONEY if val > 0 else 0.0
    return round(min(max(val, 0.0), MAX_MONEY), 2)


def parse_qty(raw, field: str = "Stock"):
    """-> (int_value, error_or_None)."""
    import math
    text = str(raw if raw is not None else "").strip().replace(",", "")
    if text == "":
        return 0, None
    try:
        val = float(text)
    except (TypeError, ValueError):
        return 0, f"{field} must be a whole number."
    if not math.isfinite(val):
        return 0, f"{field} must be a finite number."
    if val != int(val):
        return 0, f"{field} must be a whole number."
    val = int(val)
    if val < 0:
        return 0, f"{field} can't be negative."
    if val > MAX_QTY:
        return 0, f"{field} is too large."
    return val, None


# CSV cells beginning with these are executed as formulas by Excel / Sheets.
_CSV_RISKY = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Neutralise spreadsheet formula injection on export (L-01)."""
    if value is None:
        return ""
    if isinstance(value, str) and value[:1] in _CSV_RISKY:
        return "'" + value
    return value


def delete_media(url: Optional[str]):
    """Remove a locally stored upload; ignores external URLs."""
    if not url or not url.startswith("/media/products/"):
        return
    path = os.path.join(MEDIA_DIR, "products", os.path.basename(url))
    try:
        os.remove(path)
    except OSError:
        pass
