import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import __version__, main
from app.config import Settings, get_settings
from app.db import Base
from app.security import MIN_API_KEY_LENGTH, auth_posture

KEY = "k" * 40


@pytest.fixture
def client_for(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _get_db():
        with Session() as session:
            yield session

    # /api/health calls the local model; keep it offline.
    monkeypatch.setattr(main.LocalAI, "health", lambda self: {"ok": False, "error": "offline in tests"})

    def make(client=("203.0.113.9", 50000), **overrides):
        settings = Settings(_env_file=None, **overrides)
        main.app.dependency_overrides[get_settings] = lambda: settings
        main.app.dependency_overrides[main.get_db] = _get_db
        return TestClient(main.app, client=client)

    yield make
    main.app.dependency_overrides.clear()


@pytest.mark.parametrize("overrides, mode", [
    ({"api_key": KEY}, "api_key"),
    ({"api_key": "short"}, "misconfigured"),
    ({}, "misconfigured"),
    ({"local_dev_mode": True, "host": "127.0.0.1"}, "local_dev"),
    ({"local_dev_mode": True, "host": "192.0.2.10"}, "misconfigured"),
    ({"local_dev_mode": True, "api_key": KEY, "host": "192.0.2.10"}, "api_key"),
])
def test_auth_posture(overrides, mode):
    assert auth_posture(Settings(_env_file=None, **overrides)).mode == mode
    assert MIN_API_KEY_LENGTH >= 32


def test_health_is_open_but_details_need_the_key(client_for):
    client = client_for(api_key=KEY)
    public = client.get("/api/health")
    assert public.status_code == 200
    assert public.json() == {"ok": True, "version": __version__, "auth": "api_key"}
    full = client.get("/api/health", headers={"X-API-Key": KEY}).json()
    assert full["version"] == __version__ and "flags" in full and full["allow_live_submission"] is False


def test_protected_endpoints_require_the_key(client_for):
    client = client_for(api_key=KEY)
    assert client.get("/api/flags").status_code == 401
    assert client.get("/api/flags", headers={"X-API-Key": "wrong" * 10}).status_code == 401
    assert client.get("/api/flags", headers={"X-API-Key": KEY}).status_code == 200
    assert client.get("/api/flags", headers={"Authorization": f"Bearer {KEY}"}).status_code == 200
    assert client.put("/api/flags/global_kill_switch", json={"enabled": False}).status_code == 401
    assert client.post("/api/scheduler/run").status_code == 401
    assert client.get("/api/scheduler/status").status_code == 401


@pytest.mark.parametrize("overrides", [{}, {"api_key": "too-short"}, {"local_dev_mode": True, "host": "192.0.2.10"}])
def test_unconfigured_auth_refuses_everything(client_for, overrides):
    client = client_for(client=("127.0.0.1", 50000), **overrides)
    response = client.get("/api/flags")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]
    assert client.get("/api/health").status_code == 200


def test_local_dev_mode_allows_only_direct_loopback(client_for):
    local = client_for(client=("127.0.0.1", 50000), local_dev_mode=True, host="127.0.0.1")
    assert local.get("/api/flags").status_code == 200
    # A same-host reverse proxy or tunnel shows up as loopback plus forwarding headers.
    assert local.get("/api/flags", headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 401
    assert local.get("/api/flags", headers={"CF-Connecting-IP": "198.51.100.7"}).status_code == 401
    remote = client_for(client=("198.51.100.7", 50000), local_dev_mode=True, host="127.0.0.1")
    assert remote.get("/api/flags").status_code == 401


def test_api_docs_are_not_served_outside_local_dev(client_for):
    client = client_for(api_key=KEY)
    if not main.settings.local_dev_mode:
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404
