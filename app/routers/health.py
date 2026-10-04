"""Liveness and build information endpoints, used by probes and deployment checks."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.deps import DbSession, SettingsDep
from app.schemas import HealthOut, VersionOut

router = APIRouter(tags=["service"])


@router.get("/health", response_model=HealthOut, summary="Liveness and database check")
def health(db: DbSession) -> HealthOut | JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "down"})
    return HealthOut(status="ok", database="ok")


@router.get("/version", response_model=VersionOut, summary="Which build is running")
def version(settings: SettingsDep) -> VersionOut:
    return VersionOut(
        app=settings.app_name,
        version=__version__,
        git_sha=settings.git_sha,
        build_time=settings.build_time,
        environment=settings.environment,
    )
