#!/usr/bin/env python3
"""Run on container start: create tables, seed the catalogue once, ensure an admin.

Idempotent — after the first run it only reports what's already there, so it is
safe as a Docker CMD prefix on every deploy.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, engine, SessionLocal, ensure_schema   # noqa: E402
from app.models import Product, AdminUser              # noqa: E402
from app.store import ensure_defaults                  # noqa: E402
from app import security as sec                        # noqa: E402


def main():
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    db = SessionLocal()
    try:
        ensure_defaults(db)
        if db.query(Product).count() == 0:
            print("[bootstrap] empty catalogue — seeding")
            db.close()
            from seed import main as seed_main
            seed_main()
            db = SessionLocal()
        else:
            print(f"[bootstrap] catalogue already has {db.query(Product).count()} products")

        if db.query(AdminUser).count() == 0:
            pwd = os.environ.get("ADMIN_PASSWORD")
            user = os.environ.get("ADMIN_USERNAME", "admin").lower()
            if pwd:
                problem = sec.password_problem(pwd)
                if problem:
                    print(f"[bootstrap] ADMIN_PASSWORD rejected: {problem}")
                else:
                    db.add(AdminUser(username=user, name="Owner", is_owner=True,
                                     role="owner", token_version=0,
                                     password_hash=sec.hash_password(pwd)))
                    db.commit()
                    print(f"[bootstrap] created admin '{user}'")
            else:
                print("[bootstrap] WARNING: no admin user and no ADMIN_PASSWORD set — "
                      "nobody can sign in to /admin yet")
        else:
            print(f"[bootstrap] {db.query(AdminUser).count()} admin user(s) present")
    finally:
        db.close()


if __name__ == "__main__":
    main()
