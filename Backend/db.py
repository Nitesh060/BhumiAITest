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

from sqlalchemy import create_engine
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
    try:
        _engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
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
        logger.info("Database tables verified/created")
    except Exception:
        logger.exception("init_db() failed — Farm Management features may not work")
