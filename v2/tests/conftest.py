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


def load_sample_form() -> str:
    return (FIXTURES / "sample_form.html").read_text(encoding="utf-8")
