"""
db.py
=====
Database connection setup — Phase 1 of the Bhumi AI roadmap
(Farmer/Farm persistence). Uses SQLAlchemy against Neon Postgres.

Setup (one-time, browser only):
  1. https://neon.com -> create a free project
  2. Copy the connection string shown (starts with postgresql://...)
  3. On Render -> your backend service -> Environment tab, add:
       DATABASE_URL = <the Neon connection string>
  4. Redeploy. Tables are created automatically on first app startup
     (see init_db() below, called once from app.py).

If DATABASE_URL is not set, every function in this module raises a
clear error rather than silently failing — Farm Management features
degrade to a clean "not configured" response instead of crashing the
whole app (existing calculate/scoring/enrichment flows never touch
this module, so they keep working even with no DB configured).
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

Base = declarative_base()
_engine = None
_SessionLocal = None

if DATABASE_URL:
    # Neon (and most managed Postgres) expect sslmode=require; Neon's
    # connection strings already include it, but guard just in case.
    # Only relevant for postgres — sqlite (used in local testing) doesn't
    # accept this argument at all.
    is_postgres = DATABASE_URL.startswith("postgres")
    connect_args = {"sslmode": "require"} if (is_postgres and "sslmode" not in DATABASE_URL) else {}
    if is_postgres:
        # Neon suspends compute when idle and resumes on the next connection,
        # so a pooled connection that has been sitting open is often already
        # dead. pool_pre_ping catches that. The rest matters now that
        # /calculate does a parcel lookup on every request:
        #
        #  * pool_recycle — Neon drops idle connections server-side; a
        #    connection older than this is discarded rather than handed out
        #    and found broken.
        #  * small pool — Neon's connection ceiling is low compared to
        #    self-hosted Postgres, and gunicorn multiplies whatever is set
        #    here by the worker count. Use Neon's POOLED connection string
        #    (the host with `-pooler` in it) as well; the direct host will
        #    exhaust connections under load.
        connect_args.setdefault("connect_timeout", 10)
        engine_kwargs = {
            "pool_pre_ping": True,
            "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "300")),
            "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
            "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "2")),
            "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "30")),
        }
        if "-pooler." not in DATABASE_URL:
            logger.warning(
                "DATABASE_URL does not look like Neon's pooled endpoint "
                "(no '-pooler.' in the host). Under gunicorn with several "
                "workers the direct endpoint can exhaust Neon's connection "
                "limit — prefer the pooled connection string."
            )
    else:
        engine_kwargs = {"pool_pre_ping": True}

    try:
        _engine = create_engine(DATABASE_URL, connect_args=connect_args, **engine_kwargs)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    except Exception:
        logger.exception("Failed to create database engine — Farm Management features will be unavailable")
        _engine = None
        _SessionLocal = None
else:
    logger.warning("DATABASE_URL not set — Farm Management (Farmer/Farm persistence) is disabled")


def is_db_configured() -> bool:
    return _SessionLocal is not None


def get_session():
    """Returns a new SQLAlchemy session. Caller is responsible for
    closing it (use as a context manager or in a try/finally).
    Raises RuntimeError with a clear message if no DB is configured.
    """
    if not is_db_configured():
        raise RuntimeError("Database not configured — set DATABASE_URL to enable Farm Management")
    return _SessionLocal()


def _ensure_token_version_column(engine) -> None:
    """Additive, idempotent migration for User.token_version (added for
    logout/token-revocation support — see auth_service.py).

    This app has no Alembic/migration framework, and
    Base.metadata.create_all() only creates tables that don't exist
    yet — it never alters an EXISTING table to add a column a newer
    model version defines. Without this, a database that already has a
    `users` table (i.e. any live deployment with real registered users)
    would silently NOT get this column, and every query touching User
    rows would then fail with a real SQL error on the next deploy —
    breaking login/auth entirely. Safe to call on every startup: it
    only runs the ALTER TABLE once, the first time it finds the column
    missing, and never touches anything else.
    """
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return  # brand-new DB — create_all() above already built it with this column
    existing_columns = {c["name"] for c in inspector.get_columns("users")}
    if "token_version" in existing_columns:
        return
    logger.info("Auto-migrating: adding users.token_version column (existing table predates it)")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"))


def init_db():
    """Creates all tables if they don't exist yet. Safe to call on
    every app startup — no-op if tables already exist. No-op (with a
    log warning) if DATABASE_URL isn't set, so this never crashes
    app startup for people who haven't configured a DB yet.
    """
    if not is_db_configured():
        logger.warning("init_db() skipped — DATABASE_URL not set")
        return
    import models  # noqa: F401 — registers models on Base before create_all
    try:
        Base.metadata.create_all(bind=_engine)
        _ensure_token_version_column(_engine)
        logger.info("Database tables verified/created")
    except Exception:
        logger.exception("init_db() failed — Farm Management features may not work")
