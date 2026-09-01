from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.repository import Repository
from app.services.pipeline import Pipeline

security = HTTPBasic(auto_error=False)


def require_admin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    settings = request.app.state.settings
    valid = False
    if credentials is not None:
        valid = secrets.compare_digest(credentials.username, settings.admin_username) and secrets.compare_digest(
            credentials.password, settings.admin_password
        )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def get_repository(request: Request) -> Repository:
    return request.app.state.repository


def get_pipeline(request: Request) -> Pipeline:
    return request.app.state.services.pipeline
