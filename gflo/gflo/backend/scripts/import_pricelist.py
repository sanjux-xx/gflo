#!/usr/bin/env python3
"""Load (or refresh) the G-FLO 2026 price-list products only.

    python scripts/import_pricelist.py            # add new / refresh price-list rows
    python scripts/import_pricelist.py --prices   # only update rates, nothing else

Source data: data/pl_products.json (edit that file when the printed list changes).
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, engine, SessionLocal          # noqa: E402
from app.store import ensure_defaults                  # noqa: E402
from seed import seed_pricelist, seed_taxonomy         # noqa: E402


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        ensure_defaults(db)
        seed_taxonomy(db)
        seed_pricelist(db, prices_only="--prices" in sys.argv)
    finally:
        db.close()


if __name__ == "__main__":
    main()
