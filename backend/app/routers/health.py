from fastapi import APIRouter

from app.schemas import HealthOut

router = APIRouter(prefix="/api", tags=["health"])

APP_VERSION = "0.1.0"


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    """Unauthenticated liveness check (used by Docker/monitoring probes).

    Every other /api/* route requires X-API-Key per docs/ARCHITECTURE.md
    section 5; this one is deliberately left open so container health
    checks and uptime monitors don't need the secret key.
    """
    return HealthOut(status="ok", version=APP_VERSION)
