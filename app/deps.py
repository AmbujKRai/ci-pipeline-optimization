"""Shared FastAPI dependencies: settings, services and the authenticated user."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.models import User
from app.security import AuthError, decode_access_token
from app.services.notifications import Notifier
from app.services.payments import PaymentGateway

bearer_scheme = HTTPBearer(
    auto_error=False, description="Paste the access_token returned by POST /auth/login."
)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_gateway(request: Request) -> PaymentGateway:
    return request.app.state.payment_gateway


def get_notifier(request: Request) -> Notifier:
    return request.app.state.notifier


DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
GatewayDep = Annotated[PaymentGateway, Depends(get_gateway)]
NotifierDep = Annotated[Notifier, Depends(get_notifier)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(db: DbSession, settings: SettingsDep, credentials: Credentials) -> User:
    if credentials is None:
        raise _unauthorized("not authenticated")
    try:
        payload = decode_access_token(
            credentials.credentials, settings.jwt_secret, settings.jwt_algorithm
        )
        user_id = int(payload["sub"])
    except (AuthError, KeyError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, AuthError) else "invalid token"
        raise _unauthorized(detail) from None
    user = db.get(User, user_id)
    if user is None:
        raise _unauthorized("user no longer exists")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin access required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
