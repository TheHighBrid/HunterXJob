"""Red-green tests for the per-client API guardrails in app.rate_limit."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main, rate_limit
from app.config import Settings, get_settings
from app.db import Base
from app.rate_limit import ClientGuard

KEY = "r" * 40
REMOTE = ("203.0.113.50", 50000)
LOOPBACK = ("127.0.0.1", 50000)


@pytest.fixture
def make_client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _get_db():
        with Session() as session:
            yield session

    monkeypatch.setattr(rate_limit, "guard", ClientGuard())

    def make(client=REMOTE, **overrides):
        settings = Settings(_env_file=None, **overrides)
        main.app.dependency_overrides[get_settings] = lambda: settings
        main.app.dependency_overrides[main.get_db] = _get_db
        return TestClient(main.app, client=client)

    yield make
    main.app.dependency_overrides.clear()


def test_request_ceiling_returns_429_with_retry_after(make_client, monkeypatch):
    monkeypatch.setenv("HUNTERX_API_RATE_LIMIT_PER_MINUTE", "3")
    client = make_client(api_key=KEY)
    headers = {"X-API-Key": KEY}
    for _ in range(3):
        assert client.get("/api/auth/check", headers=headers).status_code == 200
    over = client.get("/api/auth/check", headers=headers)
    assert over.status_code == 429
    assert int(over.headers["Retry-After"]) >= 1


def test_ceiling_also_guards_materials_router(make_client, monkeypatch):
    monkeypatch.setenv("HUNTERX_API_RATE_LIMIT_PER_MINUTE", "1")
    client = make_client(api_key=KEY)
    headers = {"X-API-Key": KEY}
    assert client.get("/api/profile", headers=headers).status_code != 429
    assert client.get("/api/profile", headers=headers).status_code == 429


def test_failed_auth_lockout_blocks_even_the_correct_key(make_client, monkeypatch):
    monkeypatch.setenv("HUNTERX_API_AUTH_FAILURE_LIMIT", "3")
    client = make_client(api_key=KEY)
    for _ in range(3):
        assert client.get("/api/auth/check", headers={"X-API-Key": "wrong" * 8}).status_code == 401
    locked = client.get("/api/auth/check", headers={"X-API-Key": KEY})
    assert locked.status_code == 429
    assert "failed authentication" in locked.json()["detail"]


def test_below_failure_limit_correct_key_still_works(make_client, monkeypatch):
    monkeypatch.setenv("HUNTERX_API_AUTH_FAILURE_LIMIT", "3")
    client = make_client(api_key=KEY)
    for _ in range(2):
        assert client.get("/api/auth/check").status_code == 401
    assert client.get("/api/auth/check", headers={"X-API-Key": KEY}).status_code == 200


def test_lockout_is_per_client(make_client, monkeypatch):
    monkeypatch.setenv("HUNTERX_API_AUTH_FAILURE_LIMIT", "2")
    attacker = make_client(api_key=KEY)
    for _ in range(2):
        attacker.get("/api/auth/check")
    assert attacker.get("/api/auth/check", headers={"X-API-Key": KEY}).status_code == 429
    phone = TestClient(main.app, client=("198.51.100.7", 50000))
    assert phone.get("/api/auth/check", headers={"X-API-Key": KEY}).status_code == 200


def test_direct_loopback_is_never_throttled(make_client, monkeypatch):
    monkeypatch.setenv("HUNTERX_API_RATE_LIMIT_PER_MINUTE", "1")
    client = make_client(client=LOOPBACK, api_key=KEY)
    for _ in range(5):
        assert client.get("/api/auth/check", headers={"X-API-Key": KEY}).status_code == 200


def test_proxied_loopback_is_throttled(make_client, monkeypatch):
    """A tunnel on the same machine looks like 127.0.0.1; proxy headers expose it."""
    monkeypatch.setenv("HUNTERX_API_RATE_LIMIT_PER_MINUTE", "1")
    client = make_client(client=LOOPBACK, api_key=KEY)
    headers = {"X-API-Key": KEY, "X-Forwarded-For": "203.0.113.77"}
    assert client.get("/api/auth/check", headers=headers).status_code == 200
    assert client.get("/api/auth/check", headers=headers).status_code == 429


def test_misconfigured_auth_still_returns_503_not_429(make_client):
    client = make_client(api_key="")
    assert client.get("/api/auth/check").status_code == 503


def test_zero_disables_limits(make_client, monkeypatch):
    monkeypatch.setenv("HUNTERX_API_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("HUNTERX_API_AUTH_FAILURE_LIMIT", "0")
    client = make_client(api_key=KEY)
    for _ in range(5):
        assert client.get("/api/auth/check").status_code == 401
    assert client.get("/api/auth/check", headers={"X-API-Key": KEY}).status_code == 200


@pytest.mark.parametrize("raw", ["abc", "-5", ""])
def test_invalid_env_falls_back_to_default(monkeypatch, raw):
    monkeypatch.setenv("HUNTERX_API_RATE_LIMIT_PER_MINUTE", raw)
    assert rate_limit._env_int("HUNTERX_API_RATE_LIMIT_PER_MINUTE", 600) == 600


def test_window_drains_with_fake_clock():
    now = [1000.0]
    guard = ClientGuard(clock=lambda: now[0])
    assert guard.hit("c", 2, 60) == 0
    assert guard.hit("c", 2, 60) == 0
    assert guard.hit("c", 2, 60) > 0
    now[0] += 61
    assert guard.hit("c", 2, 60) == 0
    for _ in range(2):
        guard.record_failure("c", 300)
    assert guard.locked_out("c", 2, 300) > 0
    now[0] += 301
    assert guard.locked_out("c", 2, 300) == 0


# ------------------------------------------------- batch review isolation (remote_api._run_batch)

from sqlalchemy.exc import OperationalError  # noqa: E402

from app import remote_api  # noqa: E402
from app.review_actions import ReviewActionError  # noqa: E402


class _FakeSession:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def test_batch_reports_known_failures_and_keeps_going():
    db = _FakeSession()

    def action(task_id):
        if task_id == "missing":
            raise ReviewActionError(404, "review task not found")
        if task_id == "bad":
            raise ValueError("bad input")
        return {"id": task_id}

    out = remote_api._run_batch(db, ["a", "missing", "bad", "b"], action)
    assert (out["total"], out["succeeded"], out["failed"]) == (4, 2, 2)
    by_id = {r["task_id"]: r for r in out["results"]}
    assert by_id["missing"]["status_code"] == 404
    assert by_id["bad"]["status_code"] == 400
    assert db.rollbacks == 0


def test_batch_rolls_back_db_errors_without_leaking_them():
    db = _FakeSession()

    def action(task_id):
        if task_id == "locked":
            raise OperationalError("UPDATE secret_table", {}, Exception("database is locked"))
        return {"id": task_id}

    out = remote_api._run_batch(db, ["locked", "ok"], action)
    assert out["succeeded"] == 1 and db.rollbacks == 1
    failed = out["results"][0]
    assert failed == {"task_id": "locked", "ok": False, "error": "database error", "status_code": 500}


def test_batch_does_not_swallow_programming_errors():
    def action(_task_id):
        raise AttributeError("bug")

    with pytest.raises(AttributeError):
        remote_api._run_batch(_FakeSession(), ["x"], action)
