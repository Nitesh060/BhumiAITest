import os
import pytest
from security_config import require_production_secrets

def test_requires_jwt_secret_in_production(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("FLASK_DEBUG", "0")
    with pytest.raises(RuntimeError):
        require_production_secrets()

def test_allows_debug_without_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("FLASK_DEBUG", "1")
    require_production_secrets()
