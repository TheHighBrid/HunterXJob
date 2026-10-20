"""FastAPI application entrypoint: mounts routers, starts the scheduler."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import init_db
from app.routers import (
    applications,
    automation,
    health,
    jobs,
    profile,
    reports,
    resume,
    settings_router,
)
from app.services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=get_settings().LOG_LEVEL)
logger = logging.getLogger("hunterxjob")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    init_db()
    start_scheduler(settings)
    logger.info("HunterXJob backend started")
    yield
    stop_scheduler()
    logger.info("HunterXJob backend stopped")


app = FastAPI(
    title="HunterXJob Backend",
    description="Self-hosted automated job-application system backend.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(automation.router)
app.include_router(reports.router)
app.include_router(settings_router.router)
app.include_router(resume.router)
app.include_router(profile.router)
