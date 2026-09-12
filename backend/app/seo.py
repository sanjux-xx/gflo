"""Search-engine and link-preview support for the storefront.

Why this file exists
--------------------
The storefront is a single-page app. Before this, every route returned the same
`gflo.html` with one fixed set of meta tags, and every route lived behind a `#`.
Two consequences:

* Anything after `#` never reaches the server and is not part of a URL's
  identity, so all 896 products were a single URL to a search engine.
* Every link shared on WhatsApp showed the same generic title and no picture,
  because there was nothing per-page for the crawler to read.

This module supplies the three things that fix that:

  page_meta()      the real title / description / image / canonical for a path
  build_sitemap()  a sitemap of the URLs that belong on a given hostname
  build_robots()   robots.txt pointing at that sitemap

Security notes, because this module is the first place untrusted catalogue text
reaches raw HTML and XML:

* Product, category and brand names are editable in the admin and arrive via
  CSV import, so they are untrusted. `_attr()` escapes for HTML attributes and
  `_xml()` for XML text. Neither is optional — a name containing a quote would
  otherwise break out of a `content="..."` attribute.
* Canonical and sitemap URLs are built from the CONFIGURED hostname, never from
  the request's Host header. Trusting Host would let anyone poison your
  canonical tags and hand your search ranking to their own domain.
* The sitemap is cached. Rendering ~1000 rows per request is a free way for
  anyone to make your database work hard.
"""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from .models import Brand, Category, Product

BRAND_NAME = "G-FLO"
DEFAULT_TITLE = "G-FLO — Genuine Appliance Spare Parts"
DEFAULT_DESC = ("G-FLO is India's premium marketplace for genuine home-appliance spare "
                "parts — mixer grinders, washing machines, refrigerators, ACs, fans, "
                "geysers & more. AI part finder, model-number compatibility, fast "
                "delivery, 7-day returns.")
# a real file in site/assets/, so a shared link always has a picture to show
DEFAULT_IMAGE = "/assets/hero-fan-white.jpg"

# Pages that must never be indexed: private, transactional, or per-visitor.
NOINDEX_PAGES = {"cart", "checkout", "account", "wishlist", "order", "search", "notfound"}

# Brand-site pages vs shop pages. Mirrors SHOP_PAGES in site/src/03-engine.html —
# if you add a route in one place, add it in the other.
SHOP_PAGES = {"categories", "category", "brands", "brand", "deals", "search",
              "product", "finder", "cart", "checkout", "order", "wishlist", "account"}

STATIC_TITLES = {
    "home": (DEFAULT_TITLE, DEFAULT_DESC),
    "categories": ("All Spare Part Categories — G-FLO",
                   "Browse genuine spare parts by appliance — fans, mixers, geysers, "
                   "pumps, power tools and more."),
    "brands": ("Shop Spare Parts by Brand — G-FLO",
               "Find genuine spare parts for every major Indian appliance brand."),
    "deals": ("Deals & Offers on Spare Parts — G-FLO",
              "Current offers on genuine appliance spare parts."),
    "finder": ("AI Spare Part Finder — G-FLO",
               "Tell us the appliance and model number and we will find the exact part."),
    "guides": ("Appliance Repair Guides — G-FLO",
               "Step-by-step repair guides for common appliance faults."),
    "support": ("Help & Support — G-FLO", "Order help, returns, warranty and fitment support."),
    "about": ("About G-FLO — Genuine Appliance Spare Parts",
              "Who we are and why we make fan components in-house."),
    "contact": ("Contact G-FLO", "Talk to us about parts, orders or fitment."),
    "policy": ("Store Policies — G-FLO", "Shipping, returns, warranty and privacy."),
    "cart": ("Cart — G-FLO", DEFAULT_DESC),
    "checkout": ("Checkout — G-FLO", DEFAULT_DESC),
    "account": ("My Account — G-FLO", DEFAULT_DESC),
    "wishlist": ("Wishlist — G-FLO", DEFAULT_DESC),
    "notfound": ("Page not found — G-FLO", DEFAULT_DESC),
}

# ------------------------------------------------------------------ escaping


def _attr(value) -> str:
    """Escape for use inside a double-quoted HTML attribute.

    Catalogue names are admin-editable and CSV-importable, i.e. untrusted. A
    name like  5" Blade "  would otherwise close the attribute and let the rest
    of the name become markup.
    """
    return (str(value or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _xml(value) -> str:
    """Escape for XML text content."""
    return (str(value or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _clip(text: str, limit: int = 300) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ------------------------------------------------------------------ routing


def page_of_path(path: str) -> Tuple[str, Optional[str]]:
    """Map a clean URL path to (page, param). Mirrors the client's Router.parse."""
    seg = [s for s in (path or "/").strip("/").split("/") if s]
    if not seg:
        return "home", None
    head = seg[0]
    one = {"categories": "categories", "brands": "brands", "deals": "deals",
           "guides": "guides", "support": "support", "cart": "cart",
           "checkout": "checkout", "wishlist": "wishlist", "about": "about",
           "contact": "contact"}
    if head in one and len(seg) == 1:
        return one[head], None
    two = {"c": "category", "b": "brand", "p": "product", "g": "guide",
           "policy": "policy", "order": "order", "account": "account",
           "finder": "finder", "search": "search"}
    if head in two:
        return two[head], "/".join(seg[1:]) or None
    if head in ("finder", "account", "policy", "search"):
        return two.get(head, "notfound"), None
    return "notfound", None


def host_for(page: str, site_host: str, store_host: str) -> str:
    """Which configured hostname owns this page."""
    if not (site_host and store_host):
        return site_host or store_host or ""
    return store_host if page in SHOP_PAGES else site_host


def _origin(host: str) -> str:
    return ("https://" + host) if host else ""


# ------------------------------------------------------------------ meta tags


def page_meta(path: str, db: Session, site_host: str = "", store_host: str = "") -> dict:
    """Real title / description / image / canonical for one clean URL."""
    page, param = page_of_path(path)
    title, desc = STATIC_TITLES.get(page, (DEFAULT_TITLE, DEFAULT_DESC))
    image = DEFAULT_IMAGE

    try:
        if page == "product" and param:
            p = db.query(Product).filter(Product.sku == param).first()
            if p:
                cat = db.get(Category, p.category_id) if p.category_id else None
                title = f"{p.name} — Genuine Spare Part | {BRAND_NAME}"
                # A description is what shows under the link in a search result,
                # so it has to read like a sentence, not a field dump.
                desc = _clip(p.description or
                             (f"Buy the genuine {p.name}"
                              + (f" for {cat.name} appliances" if cat else "")
                              + f". Part code {p.sku}."
                              + " Model-number fitment check, fast delivery"
                                " across India and 7-day returns."))
                if p.image_url:
                    image = p.image_url
            else:
                page, title = "notfound", STATIC_TITLES["notfound"][0]
        elif page == "category" and param:
            c = db.get(Category, param)
            if c:
                n = db.query(Product).filter(Product.category_id == c.id).count()
                title = f"{c.name} Spare Parts — {BRAND_NAME}"
                desc = _clip(c.description or
                             f"{n} genuine {c.name} spare parts, with model-number "
                             f"compatibility and 7-day returns.")
                if c.image_url:
                    image = c.image_url
            else:
                page, title = "notfound", STATIC_TITLES["notfound"][0]
        elif page == "brand" and param:
            b = db.get(Brand, param)
            if b:
                title = f"{b.name} Appliance Spare Parts — {BRAND_NAME}"
                desc = _clip(f"Genuine spare parts compatible with {b.name} appliances.")
            else:
                page, title = "notfound", STATIC_TITLES["notfound"][0]
    except Exception:
        # Meta tags are decoration; never let a lookup take the page down.
        page, param = page_of_path(path)

    own_host = host_for(page, site_host, store_host)
    canonical = (_origin(own_host) + path) if own_host else path
    if image.startswith("/") and own_host:
        image = _origin(own_host) + image

    return {"page": page, "title": title, "description": desc, "image": image,
            "canonical": canonical, "noindex": page in NOINDEX_PAGES}


def meta_html(meta: dict) -> str:
    """The tag block injected into <head>. Every value escaped."""
    t, d = _attr(meta["title"]), _attr(meta["description"])
    rows = [
        f'<title>{_attr(meta["title"])}</title>',
        f'<meta name="description" content="{d}">',
        f'<link rel="canonical" href="{_attr(meta["canonical"])}">',
        f'<meta property="og:title" content="{t}">',
        f'<meta property="og:description" content="{d}">',
        f'<meta property="og:image" content="{_attr(meta["image"])}">',
        f'<meta property="og:url" content="{_attr(meta["canonical"])}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{BRAND_NAME}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{t}">',
        f'<meta name="twitter:description" content="{d}">',
        f'<meta name="twitter:image" content="{_attr(meta["image"])}">',
    ]
    if meta["noindex"]:
        rows.append('<meta name="robots" content="noindex,follow">')
    return "".join(rows)


# ------------------------------------------------------------------ sitemap

# A sitemap may only list URLs on its own hostname, so each host gets its own.
_SITEMAP_TTL = 900
_sitemap_cache: dict = {}


def _urls_for_host(db: Session, host: str, site_host: str, store_host: str):
    """(loc, changefreq, priority) for every indexable page owned by `host`."""
    split = bool(site_host and store_host)
    out = []

    def add(path: str, page: str, freq: str, pri: str):
        if page in NOINDEX_PAGES:
            return
        if split and host_for(page, site_host, store_host) != host:
            return
        out.append((_origin(host) + path, freq, pri))

    add("/", "home", "weekly", "1.0")
    add("/categories", "categories", "weekly", "0.9")
    add("/brands", "brands", "weekly", "0.7")
    add("/deals", "deals", "daily", "0.7")
    add("/finder", "finder", "monthly", "0.6")
    add("/guides", "guides", "monthly", "0.6")
    add("/support", "support", "yearly", "0.4")
    add("/about", "about", "yearly", "0.4")
    add("/contact", "contact", "yearly", "0.4")
    for key in ("terms", "privacy", "shipping", "returns", "warranty"):
        add("/policy/" + key, "policy", "yearly", "0.3")

    for c in db.query(Category).all():
        add("/c/" + str(c.id), "category", "weekly", "0.8")
    for b in db.query(Brand).all():
        add("/b/" + str(b.id), "brand", "monthly", "0.5")
    # only products a visitor can actually buy
    for (sku,) in db.query(Product.sku).filter(Product.visible == True).all():  # noqa: E712
        add("/p/" + str(sku), "product", "weekly", "0.7")
    return out


def build_sitemap(db: Session, host: str, site_host: str = "", store_host: str = "") -> str:
    hit = _sitemap_cache.get(host)
    if hit and time.time() - hit[0] < _SITEMAP_TTL:
        return hit[1]

    rows = _urls_for_host(db, host, site_host, store_host)
    body = "".join(
        f"<url><loc>{_xml(loc)}</loc>"
        f"<changefreq>{freq}</changefreq>"
        f"<priority>{pri}</priority></url>"
        for loc, freq, pri in rows)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + body + "</urlset>")
    _sitemap_cache[host] = (time.time(), xml)
    return xml


def sitemap_url_count(db: Session, host: str, site_host: str = "", store_host: str = "") -> int:
    return len(_urls_for_host(db, host, site_host, store_host))


def clear_sitemap_cache():
    _sitemap_cache.clear()


# ------------------------------------------------------------------ robots


def build_robots(host: str) -> str:
    lines = [
        "User-agent: *",
        "Allow: /",
        # nothing private, transactional or per-visitor should be crawled
        "Disallow: /admin",
        "Disallow: /api/",
        "Disallow: /cart",
        "Disallow: /checkout",
        "Disallow: /account",
        "Disallow: /wishlist",
        "Disallow: /order/",
        "Disallow: /search/",
        "",
    ]
    if host:
        lines.append(f"Sitemap: {_origin(host)}/sitemap.xml")
        lines.append("")
    return "\n".join(lines)
