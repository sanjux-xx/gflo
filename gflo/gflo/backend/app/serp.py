"""SerpAPI client — Google results for admin-side catalogue research.

The key is read from the SERPAPI_KEY environment variable only. It is never
written to the database, never rendered into a page, and never logged: set it as
a secret on your host (Render/Northflank) exactly like SECRET_KEY.

Every call costs a paid SerpAPI credit, so callers MUST be behind admin auth —
an unauthenticated endpoint here is a way for a stranger to burn your quota.
"""
import os
from typing import Optional

# Read lazily via _key() rather than at import time so the module still imports
# (and configured() reports False) when the variable isn't set.
ENV_VAR = "SERPAPI_KEY"

# Keep requests short: a slow upstream otherwise ties up a worker.
TIMEOUT_SECONDS = int(os.environ.get("SERPAPI_TIMEOUT", 12))
DEFAULT_COUNTRY = os.environ.get("SERPAPI_COUNTRY", "in")     # India — matches the catalogue
DEFAULT_LOCALE = os.environ.get("SERPAPI_LOCALE", "en")
MAX_QUERY_CHARS = 200


class SerpError(RuntimeError):
    """Raised with a message that is safe to show an admin."""


def _key() -> Optional[str]:
    val = (os.environ.get(ENV_VAR) or "").strip()
    return val or None


def configured() -> bool:
    """True when a key is present. Use this to hide/disable SerpAPI features."""
    return _key() is not None


def status() -> dict:
    """Non-secret description of the current configuration, safe to render."""
    key = _key()
    return {
        "configured": key is not None,
        # a fingerprint, never the key itself — enough to tell two keys apart
        "key_hint": (key[:4] + "…" + key[-4:]) if key and len(key) > 12 else ("set" if key else ""),
        "country": DEFAULT_COUNTRY,
        "locale": DEFAULT_LOCALE,
        "timeout": TIMEOUT_SECONDS,
    }


def _run(params: dict) -> dict:
    key = _key()
    if not key:
        raise SerpError("SerpAPI isn't configured. Set the SERPAPI_KEY environment variable.")
    payload = dict(params)
    payload.update({"api_key": key, "gl": DEFAULT_COUNTRY, "hl": DEFAULT_LOCALE,
                    "timeout": TIMEOUT_SECONDS})
    try:
        from serpapi import GoogleSearch
    except ImportError:                      # dependency not installed
        raise SerpError("The google-search-results package isn't installed.")
    try:
        data = GoogleSearch(payload).get_dict()
    except Exception as exc:
        # never surface the key or a raw traceback
        raise SerpError(f"Couldn't reach SerpAPI ({type(exc).__name__}).")
    if isinstance(data, dict) and data.get("error"):
        raise SerpError(str(data["error"])[:300])
    return data or {}


def _clean_query(query: str) -> str:
    q = " ".join((query or "").split())[:MAX_QUERY_CHARS]
    if not q:
        raise SerpError("Enter something to search for.")
    return q


def shopping(query: str, limit: int = 8) -> list:
    """Google Shopping hits for a part — useful for sanity-checking a rate.

    -> [{title, price, extracted_price, source, link, thumbnail}]
    """
    data = _run({"engine": "google_shopping", "q": _clean_query(query)})
    out = []
    for row in (data.get("shopping_results") or [])[:max(1, min(limit, 20))]:
        out.append({
            "title": row.get("title") or "",
            "price": row.get("price") or "",
            "extracted_price": row.get("extracted_price"),
            "source": row.get("source") or "",
            "link": row.get("product_link") or row.get("link") or "",
            "thumbnail": row.get("thumbnail") or "",
        })
    return out


def images(query: str, limit: int = 12) -> list:
    """Google Images hits — candidate photos for a product that has none.

    -> [{thumbnail, original, title, source}]
    Returns URLs only; nothing is downloaded here. Whatever you save still goes
    through store.save_upload(), which re-validates the bytes as a real image.
    """
    data = _run({"engine": "google_images", "q": _clean_query(query)})
    out = []
    for row in (data.get("images_results") or [])[:max(1, min(limit, 40))]:
        original = row.get("original") or ""
        if not original.startswith(("http://", "https://")):
            continue
        out.append({
            "thumbnail": row.get("thumbnail") or "",
            "original": original,
            "title": row.get("title") or "",
            "source": row.get("source") or row.get("link") or "",
        })
    return out


def web(query: str, limit: int = 8) -> list:
    """Plain Google results -> [{title, link, snippet}]."""
    data = _run({"engine": "google", "q": _clean_query(query)})
    out = []
    for row in (data.get("organic_results") or [])[:max(1, min(limit, 20))]:
        out.append({"title": row.get("title") or "", "link": row.get("link") or "",
                    "snippet": row.get("snippet") or ""})
    return out
