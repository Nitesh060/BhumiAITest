"""Regression tests for the token-revocation audit fix:

Previously there was no way to invalidate a JWT before its natural
expiry (TOKEN_EXPIRY_HOURS). Deleting a user, or any equivalent of a
"logout", did not stop their existing, still-unexpired token from
continuing to authenticate successfully — require_auth only checked
the JWT's own signature/expiry, never the DB.

Fix: a `token_version` counter on User, embedded in every issued JWT
(auth_service.generate_token) and re-checked against the live DB value
on every authenticated request (auth_service._token_version_still_valid,
wired into require_auth). Bumping it (auth_service.bump_token_version,
called from the new /auth/logout route) invalidates every token issued
before that point, immediately.

Since this app has no Alembic and Base.metadata.create_all() never
alters an existing table, db.py additionally needs an additive,
idempotent migration (_ensure_token_version_column) so a live database
that already has a `users` table (predating this column) doesn't break
on the next deploy.
"""
import os
import sys

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Base, _ensure_token_version_column
import auth_service
from models import User


# ---------------------------------------------------------------------------
# Additive migration: users table predating the token_version column
# ---------------------------------------------------------------------------

class TestTokenVersionMigration:
    def _make_legacy_users_table(self, engine):
        """Simulates a live DB whose `users` table was created before
        token_version existed — i.e. Base.metadata.create_all() is not
        involved at all, only the raw legacy schema."""
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE users ("
                "id VARCHAR PRIMARY KEY, "
                "username VARCHAR NOT NULL UNIQUE, "
                "password_hash VARCHAR NOT NULL, "
                "name VARCHAR NOT NULL, "
                "role VARCHAR NOT NULL, "
                "created_at DATETIME)"
            ))
            conn.execute(text(
                "INSERT INTO users (id, username, password_hash, name, role) "
                "VALUES ('u1', 'ram', 'hash', 'Ram', 'field_officer')"
            ))

    def test_adds_missing_column_to_pre_existing_table(self):
        engine = create_engine("sqlite:///:memory:")
        self._make_legacy_users_table(engine)

        inspector = inspect(engine)
        assert "token_version" not in {c["name"] for c in inspector.get_columns("users")}

        _ensure_token_version_column(engine)

        inspector = inspect(engine)
        assert "token_version" in {c["name"] for c in inspector.get_columns("users")}

    def test_backfills_existing_rows_with_zero(self):
        engine = create_engine("sqlite:///:memory:")
        self._make_legacy_users_table(engine)
        _ensure_token_version_column(engine)

        with engine.connect() as conn:
            value = conn.execute(text("SELECT token_version FROM users WHERE id = 'u1'")).scalar()
        assert value == 0

    def test_idempotent_when_column_already_present(self):
        """Must be safe to call on every startup, not just once ever —
        a second call against a table that already has the column must
        not raise (e.g. a duplicate-column SQL error)."""
        engine = create_engine("sqlite:///:memory:")
        self._make_legacy_users_table(engine)
        _ensure_token_version_column(engine)
        _ensure_token_version_column(engine)  # must not raise

    def test_noop_when_table_does_not_exist_yet(self):
        """A brand-new DB: create_all() (called before this function, in
        init_db()) already builds `users` with the column from the
        model, so there's nothing for this function to do."""
        engine = create_engine("sqlite:///:memory:")
        _ensure_token_version_column(engine)  # must not raise
        assert "users" not in inspect(engine).get_table_names()


# ---------------------------------------------------------------------------
# Token issuance embeds the current token_version
# ---------------------------------------------------------------------------

class TestTokenGeneration:
    @pytest.fixture
    def db_session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        yield session
        session.close()

    def test_generated_token_embeds_current_token_version(self, db_session):
        user = auth_service.register_user(db_session, "sita", "password123", "Sita")
        user.token_version = 3
        db_session.commit()

        token = auth_service.generate_token(user)
        payload = auth_service.decode_token(token)

        assert payload["token_version"] == 3


# ---------------------------------------------------------------------------
# Revocation logic: bump_token_version / _token_version_still_valid
# ---------------------------------------------------------------------------

class TestRevocationLogic:
    @pytest.fixture
    def db_session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        yield session
        session.close()

    def test_fresh_token_is_valid(self, db_session):
        user = auth_service.register_user(db_session, "gita", "password123", "Gita")
        token = auth_service.generate_token(user)
        payload = auth_service.decode_token(token)

        assert auth_service._token_version_still_valid(db_session, payload) is True

    def test_bump_invalidates_previously_issued_token(self, db_session):
        """The actual production bug this fixes: a token issued before
        logout must stop authenticating immediately after logout,
        without waiting for its natural expiry."""
        user = auth_service.register_user(db_session, "mohan", "password123", "Mohan")
        token = auth_service.generate_token(user)
        payload = auth_service.decode_token(token)

        auth_service.bump_token_version(db_session, user)

        assert auth_service._token_version_still_valid(db_session, payload) is False

    def test_new_token_after_bump_is_valid(self, db_session):
        user = auth_service.register_user(db_session, "kiran", "password123", "Kiran")
        auth_service.bump_token_version(db_session, user)

        new_token = auth_service.generate_token(user)
        new_payload = auth_service.decode_token(new_token)

        assert auth_service._token_version_still_valid(db_session, new_payload) is True

    def test_deleted_user_invalidates_token(self, db_session):
        user = auth_service.register_user(db_session, "deleted_user", "password123", "Deleted")
        token = auth_service.generate_token(user)
        payload = auth_service.decode_token(token)

        db_session.delete(user)
        db_session.commit()

        assert auth_service._token_version_still_valid(db_session, payload) is False

    def test_bump_is_per_user(self, db_session):
        """Logging out one user must not affect another user's tokens."""
        user_a = auth_service.register_user(db_session, "user_a", "password123", "A")
        user_b = auth_service.register_user(db_session, "user_b", "password123", "B")
        token_b = auth_service.generate_token(user_b)
        payload_b = auth_service.decode_token(token_b)

        auth_service.bump_token_version(db_session, user_a)

        assert auth_service._token_version_still_valid(db_session, payload_b) is True
