import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """The suite must never touch the network. httpx.MockTransport is unaffected."""

    def refuse(self, request):  # pragma: no cover - only hit on a bug
        raise RuntimeError(f"network access disabled in tests: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


def load_fixture_json(name: str) -> dict:
    return json.loads((FIXTURES / "greenhouse" / name).read_text(encoding="utf-8"))


def load_ats_fixture(name: str) -> str:
    return (FIXTURES / "ats" / name).read_text(encoding="utf-8")


def load_ats_json(name: str):
    return json.loads(load_ats_fixture(name))


def load_sample_form() -> str:
    return (FIXTURES / "sample_form.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _posting_is_live(monkeypatch):
    """Liveness probes default to "live" offline; liveness tests inject their own probe."""
    from app import job_liveness

    def live(job):
        return job_liveness.Observation(job_liveness.LIVE, "test_stub", 200, "offline test stub")

    monkeypatch.setattr(job_liveness, "probe_posting", lambda job, **_kwargs: live(job))
