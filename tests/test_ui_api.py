"""UI API tests (M6 endpoints; no targets or model key needed)."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from chainreaction.web import app  # noqa: E402

client = TestClient(app)


def test_status_shape():
    body = client.get("/api/status").json()
    assert set(body) == {"key_present", "live_available", "version"}
    assert isinstance(body["version"], str) and body["version"]
    assert body["live_available"] == body["key_present"]


def test_status_no_key_by_default(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    body = client.get("/api/status").json()
    assert body["key_present"] is False
    assert body["live_available"] is False


def test_status_with_key(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    body = client.get("/api/status").json()
    assert body["key_present"] is True
    assert body["live_available"] is True
