"""
auth_service.py
================
Simple JWT-based auth for two roles: "admin" and "field_officer".

Not a full identity system — no email verification, password reset,
or refresh tokens. Good enough for an internal team tool; revisit if
this ever needs to face external users.

Bootstrap: the FIRST user ever registered automatically becomes an
admin (so there's always at least one). Every user after that must be
created by an existing admin via POST /auth/register with a valid
admin token.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Optional

import jwt
from flask import request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import Session

from models import User

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET", "dev-insecure-secret-change-me")
JWT_ALGO = "HS256"
TOKEN_EXPIRY_HOURS = 12

if JWT_SECRET == "dev-insecure-secret-change-me":
    logger.warning("JWT_SECRET not set — using an insecure default. Set JWT_SECRET on the server before real use.")


def register_user(db: Session, username: str, password: str, name: str, role: str = "field_officer") -> User:
    is_first_user = db.query(User).count() == 0
    final_role = "admin" if is_first_user else role
    if final_role not in ("admin", "field_officer"):
        final_role = "field_officer"

    user = User(username=username, password_hash=generate_password_hash(password), name=name, role=final_role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.username == username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return None
    return user


def generate_token(user: User) -> str:
    payload = {
        "user_id": user.id, "username": user.username, "role": user.role, "name": user.name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def require_auth(roles: Optional[list] = None):
    """Route decorator. Usage: @require_auth() or @require_auth(["admin"]).
    Expects an `Authorization: Bearer <token>` header. On success,
    attaches the decoded payload to `request.user`.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "Missing or invalid Authorization header"}), 401

            token = auth_header[len("Bearer "):]
            payload = decode_token(token)
            if not payload:
                return jsonify({"error": "Invalid or expired token"}), 401

            if roles and payload.get("role") not in roles:
                return jsonify({"error": "Insufficient permissions for this action"}), 403

            request.user = payload
            return fn(*args, **kwargs)
        return wrapper
    return decorator
