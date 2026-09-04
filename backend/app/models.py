"""Catalogue schema. Everything the storefront shows lives here and is editable in /admin."""
import datetime as dt
from sqlalchemy import (Column, Integer, String, Float, Boolean, Text, DateTime,
                        ForeignKey, Index)
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
