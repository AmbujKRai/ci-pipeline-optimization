"""Registration, login and the current user."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, SettingsDep
from app.models import User
from app.schemas import LoginRequest, TokenOut, UserCreate, UserOut
from app.security import create_access_token, hash_password, validate_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: DbSession, settings: SettingsDep) -> User:
    validate_password(payload.password)
    if db.scalar(select(User.id).where(User.email == payload.email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "an account with this e-mail already exists")
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password, settings.bcrypt_rounds),
        role="customer",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, db: DbSession, settings: SettingsDep) -> TokenOut:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "incorrect e-mail or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(
        subject=str(user.id),
        role=user.role,
        secret=settings.jwt_secret,
        expires_minutes=settings.jwt_expiry_minutes,
        algorithm=settings.jwt_algorithm,
    )
    return TokenOut(access_token=token, expires_in=settings.jwt_expiry_minutes * 60)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
