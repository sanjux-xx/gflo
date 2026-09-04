#!/usr/bin/env python3
"""Create or reset an admin login.

    python scripts/create_admin.py                      # prompts for details
    python scripts/create_admin.py raj 'my long pass'    # non-interactive
    python scripts/create_admin.py raj 'new pass' --reset   # change an existing password

Passwords are stored as scrypt hashes — the plain text is never saved anywhere.
"""
import getpass, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func                             # noqa: E402
from app.db import Base, engine, SessionLocal, ensure_schema   # noqa: E402
from app.models import AdminUser                        # noqa: E402
from app import security as sec                         # noqa: E402


def main():
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    reset = "--reset" in sys.argv

    username = (args[0] if args else input("Admin username: ")).strip().lower()
    if not username:
        sys.exit("A username is required.")
    if len(args) > 1:
        password = args[1]
    else:
        password = getpass.getpass("Password (min 10 chars): ")
        if password != getpass.getpass("Repeat password: "):
            sys.exit("Passwords didn't match.")
    problem = sec.password_problem(password)
    if problem:
        sys.exit(problem)

    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(func.lower(AdminUser.username) == username).first()
        if user and not reset:
            sys.exit(f"'{username}' already exists. Re-run with --reset to change the password.")
        if user:
            user.password_hash = sec.hash_password(password)
            # sign out any existing sessions for this account
            user.token_version = (user.token_version or 0) + 1
            print(f"password reset for '{username}' (existing sessions signed out)")
        else:
            first = db.query(AdminUser).count() == 0
            db.add(AdminUser(username=username, name=username.title(),
                             is_owner=first, role=("owner" if first else "editor"),
                             token_version=0, password_hash=sec.hash_password(password)))
            print(f"created admin '{username}'" + (" (owner)" if first else ""))
        db.commit()
    finally:
        db.close()
    print("sign in at /admin/login")


if __name__ == "__main__":
    main()
