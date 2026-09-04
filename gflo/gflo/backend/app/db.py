"""Database engine + session. SQLite by default, Postgres if DATABASE_URL is set."""
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

# DATA_DIR is where the SQLite file and uploaded media live. On Render/Northflank
# point it at a mounted persistent volume (e.g. /data) so nothing is lost on redeploy.
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
os.makedirs(DATA_DIR, exist_ok=True)

MEDIA_DIR = os.environ.get("MEDIA_DIR", os.path.join(DATA_DIR, "media"))
os.makedirs(os.path.join(MEDIA_DIR, "products"), exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL") or f"sqlite:///{os.path.join(DATA_DIR, 'gflo.db')}"

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, future=True)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_con, _):
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA journal_mode=WAL")      # safe concurrent reads while admin writes
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema():
    """Add columns introduced after a shipped gflo.db was built.

    Lives here (not in main.py) so the CLI scripts — create_admin.py,
    bootstrap.py, seed.py — migrate an older database too instead of failing
    on a missing column before the web app ever starts.
    """
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(admin_users)").fetchall()
        if not rows:
            return                      # table not created yet; models will make it
        cols = {r[1] for r in rows}
        if "token_version" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE admin_users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
        if "role" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE admin_users ADD COLUMN role VARCHAR(16) DEFAULT 'editor'")
            # the existing owner keeps full rights; any other pre-existing
            # account becomes an editor (least privilege) and can be promoted
            conn.exec_driver_sql("UPDATE admin_users SET role='owner' WHERE is_owner=1")
            conn.exec_driver_sql(
                "UPDATE admin_users SET role='editor' WHERE role IS NULL OR role=''")
