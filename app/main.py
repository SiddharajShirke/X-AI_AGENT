from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.db import Database
from app.repository import Repository
from app.routes import api, slack, telegram, ui
from app.services.factory import build_services
from app.services.scheduler import SchedulerService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

APP_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None, start_scheduler: bool | None = None) -> FastAPI:
    settings = settings or Settings()
    database = Database(settings.database_path)
    database.initialize()
    repository = Repository(database)
    services = build_services(settings, repository)
    scheduler = SchedulerService(settings, repository, services.pipeline)
    should_start = settings.scheduler_enabled if start_scheduler is None else start_scheduler

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task: asyncio.Task | None = None
        if should_start:
            task = asyncio.create_task(scheduler.run_forever(), name="x-agent-scheduler")
        try:
            yield
        finally:
            scheduler.stop()
            if task:
                await task

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Prototype human-in-the-loop Startup X Agent",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.repository = repository
    app.state.services = services
    app.state.scheduler = scheduler

    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
    app.include_router(ui.router)
    app.include_router(api.router)
    app.include_router(slack.router)
    app.include_router(telegram.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": settings.app_mode}

    return app


app = create_app()
