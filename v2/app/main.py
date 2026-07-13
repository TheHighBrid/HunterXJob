from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from app.ai_client import LocalAIClient
from app.config import settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict[str, object]:
    ai_ok = False
    ai_error: str | None = None
    try:
        await LocalAIClient().health()
        ai_ok = True
    except Exception as exc:  # health endpoints must report, not crash
        ai_error = str(exc)

    return {
        "status": "ok" if ai_ok else "degraded",
        "service": settings.app_name,
        "time": datetime.now(UTC).isoformat(),
        "mode": settings.application_mode,
        "ai": {
            "provider": settings.ai_provider,
            "base_url": settings.ai_base_url,
            "fast_model": settings.ai_fast_model,
            "quality_model": settings.ai_quality_model,
            "healthy": ai_ok,
            "error": ai_error,
        },
        "browser_enabled": settings.browser_enabled,
        "scheduler_enabled": settings.scheduler_enabled,
    }


@app.get("/api/config/public")
async def public_config() -> dict[str, object]:
    return {
        "mode": settings.application_mode,
        "minimum_match_score": settings.minimum_match_score,
        "max_applications_per_day": settings.max_applications_per_day,
        "target_locations": settings.target_locations.split(","),
        "target_keywords": settings.target_keywords.split(","),
    }
