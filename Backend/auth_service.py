"""
auth_service.py
================
JWT authentication for Bhumi AI's internal users.
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
ENVIRONMENT = os.getenv("ENVIRONMENT", os.getenv("FLASK_ENV", "development")).lower()
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    if ENVIRONMENT in {"production", "prod"}:
        raise RuntimeError("JWT_SECRET must be set in production")
    JWT_SECRET = "dev-insecure-secret-change-me"
    logger.warning("JWT_SECRET not set — using development-only insecure default")
JWT_ALGO = "HS256"
TOKEN_EXPIRY_HOURS = int(os.getenv("TOKEN_EXPIRY_HOURS", "12"))
MIN_PASSWORD_LENGTH = int(os.getenv("MIN_PASSWORD_LENGTH", "10"))


def register_user(db: Session, username: str, password: str, name: str, role: str = "field_officer") -> User:
    username = username.strip()
    name = name.strip()
    if not username or not name:
        raise ValueError("Username and name cannot be empty")
    is_first_user = db.query(User).count() == 0
    final_role = "admin" if is_first_user else role
    if final_role not in ("admin", "field_officer"):
        raise ValueError("Invalid role")
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
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def require_auth(roles: Optional[list] = None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "Missing or invalid Authorization header"}), 401
            payload = decode_token(auth_header[len("Bearer "):].strip())
            if not payload:
                return jsonify({"error": "Invalid or expired token"}), 401
            if roles and payload.get("role") not in roles:
                return jsonify({"error": "Insufficient permissions for this action"}), 403
            if request.method == "DELETE" and request.path.startswith(("/farmers", "/farms")) and payload.get("role") != "admin":
                return jsonify({"error": "Only an admin can delete farmer/farm records"}), 403
            request.user = payload
            return fn(*args, **kwargs)
        return wrapper
    return decorator
