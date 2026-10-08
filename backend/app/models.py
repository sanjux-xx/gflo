"""Catalogue schema. Everything the storefront shows lives here and is editable in /admin."""
import datetime as dt
from sqlalchemy import (Column, Integer, String, Float, Boolean, Text, DateTime, Date,
                        ForeignKey, Index, LargeBinary)
from sqlalchemy.orm import relationship
from .db import Base


def now():
    return dt.datetime.utcnow()


class Category(Base):
    __tablename__ = "categories"
    id = Column(String(48), primary_key=True)          # slug, e.g. "fan-pipes"
    name = Column(String(80), nullable=False)
    code = Column(String(8), default="GN")             # used in generated SKUs
    hue = Column(Integer, default=210)
    description = Column(String(240), default="")
    image_url = Column(String(400), default="")
    icon = Column(String(60), default="")
    popular = Column(Boolean, default=False)
    sort_order = Column(Integer, default=100)
    products = relationship("Product", back_populates="category")


class Brand(Base):
    __tablename__ = "brands"
    id = Column(String(48), primary_key=True)
    name = Column(String(80), nullable=False)
    hue = Column(Integer, default=210)
    sort_order = Column(Integer, default=100)


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    sku = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(240), nullable=False)
    category_id = Column(String(48), ForeignKey("categories.id"), index=True)
    part_family = Column(String(48), default="spares")   # maps to the storefront's PT_MAP
    group_name = Column(String(120), default="")         # price-list section, e.g. "Screws"

    price = Column(Float, nullable=True)                 # None -> "Price on request"
    mrp = Column(Float, nullable=True)
    stock = Column(Integer, default=0)
    unit = Column(String(24), default="piece")           # piece / packet / coil / kg / 1000 pcs

    size = Column(String(120), default="")
    pack = Column(String(120), default="")
    colours = Column(String(160), default="")
    material = Column(String(160), default="")
    warranty = Column(String(48), default="")
    weight = Column(String(48), default="")
    description = Column(Text, default="")
    brand_names = Column(String(240), default="")        # comma separated
    # Size options with their own price, e.g. "20 x 10 mm=850|25 x 10 mm=1000".
    # Empty = a normal single-price product.
    variants = Column(Text, default="")

    rating = Column(Float, default=0)
    reviews = Column(Integer, default=0)
    is_new = Column(Boolean, default=False)
    is_best = Column(Boolean, default=False)
    is_featured = Column(Boolean, default=False)
    visible = Column(Boolean, default=True, index=True)

    image_url = Column(String(500), default="")          # primary photo
    source = Column(String(24), default="manual")        # legacy / pricelist / manual
    sort_order = Column(Integer, default=1000)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    category = relationship("Category", back_populates="products")
    images = relationship("ProductImage", back_populates="product",
                          cascade="all, delete-orphan", order_by="ProductImage.sort_order")


Index("ix_products_name", Product.name)


class ProductImage(Base):
    __tablename__ = "product_images"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), index=True)
    url = Column(String(500), nullable=False)
    sort_order = Column(Integer, default=0)
    product = relationship("Product", back_populates="images")


class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    name = Column(String(80), default="")
    password_hash = Column(String(255), nullable=False)
    is_owner = Column(Boolean, default=False)      # owner cannot be deleted
    # "owner" / "admin" may manage accounts, settings and import/export;
    # "editor" may only edit the catalogue (least privilege).
    role = Column(String(16), default="editor")
    # Bumped on logout, password change and forced sign-out. Sessions carry the
    # value they were minted with, so raising it revokes every existing cookie.
    token_version = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=now)
    last_login = Column(DateTime, nullable=True)


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(64), primary_key=True)
    value = Column(Text, default="")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=now, index=True)
    username = Column(String(64), default="")
    action = Column(String(48), default="")
    entity = Column(String(48), default="")
    entity_id = Column(String(64), default="")
    detail = Column(Text, default="")


class Poster(Base):
    """Festival poster / promo banner shown on the storefront.

    style      "popup" = a poster that opens over the site when a visitor arrives
               "strip" = a slim full-width banner image above the header
    frequency  how often one visitor sees a popup: "once" (once ever),
               "daily" (once a day) or "visit" (every new browser session)
    starts_on / ends_on are inclusive dates in the shop's local time (IST by
    default); leave either blank for "from now" / "until switched off".
    """
    __tablename__ = "posters"
    id = Column(Integer, primary_key=True)
    title = Column(String(120), nullable=False)          # also the image alt text
    image_url = Column(String(500), nullable=False)      # desktop / main image
    mobile_image_url = Column(String(500), default="")   # optional portrait version
    link_url = Column(String(500), default="")           # optional click-through
    button_text = Column(String(40), default="")         # optional CTA label
    style = Column(String(16), default="popup")
    frequency = Column(String(16), default="daily")
    starts_on = Column(Date, nullable=True)
    ends_on = Column(Date, nullable=True)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class MediaFile(Base):
    """Every photo uploaded in the admin, stored in the database itself.

    The container's disk is temporary on free hosting (wiped on each redeploy),
    so the disk under MEDIA_DIR is only a cache. /media/... is served from disk
    when the file is there and from this table otherwise — uploads survive
    redeploys without paying for a volume.
    path is relative to /media/, e.g. "products/3f2a9c1e0b7d4e21.jpg".
    """
    __tablename__ = "media_files"
    path = Column(String(300), primary_key=True)
    content_type = Column(String(60), default="image/jpeg")
    size = Column(Integer, default=0)
    data = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=now)


class Order(Base):
    """An order placed on the storefront. Prices are recomputed on the server
    from the catalogue at the moment of ordering, never taken from the browser."""
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    number = Column(String(24), unique=True, index=True)     # e.g. GF1042
    created_at = Column(DateTime, default=now, index=True)
    status = Column(String(16), default="new", index=True)   # new/confirmed/packed/shipped/delivered/cancelled
    seen = Column(Boolean, default=False, index=True)         # opened in the admin yet?
    customer_name = Column(String(80), default="")
    phone = Column(String(20), default="")
    address = Column(String(300), default="")
    city = Column(String(120), default="")
    pincode = Column(String(10), default="")
    ship_method = Column(String(16), default="std")
    payment = Column(String(16), default="cod")
    coupon = Column(String(24), default="")
    items_json = Column(Text, default="[]")
    subtotal = Column(Float, default=0)
    discount = Column(Float, default=0)
    shipping = Column(Float, default=0)
    total = Column(Float, default=0)
    quote_items = Column(Integer, default=0)                  # lines with "price on request"
    note = Column(Text, default="")                           # staff note
    status_log = Column(Text, default="")                     # JSON {"new": "...Z", "shipped": "...Z"}


class Coupon(Base):
    """A discount code the owner creates in Admin -> Coupons.

    kind   "pct"  = value % off the items (optional max_discount cap)
           "flat" = value rupees off (never more than the items cost)
           "ship" = free standard delivery (express / install charges still apply)
    Times used is counted from the orders table (cancelled orders don't count).
    """
    __tablename__ = "coupons"
    id = Column(Integer, primary_key=True)
    code = Column(String(24), unique=True, index=True, nullable=False)
    kind = Column(String(8), default="pct")
    value = Column(Float, default=0)
    min_order = Column(Float, default=0)
    max_discount = Column(Float, nullable=True)
    starts_on = Column(Date, nullable=True)
    ends_on = Column(Date, nullable=True)
    usage_limit = Column(Integer, nullable=True)       # total orders; blank = unlimited
    once_per_phone = Column(Boolean, default=False)
    active = Column(Boolean, default=True)
    note = Column(String(200), default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
