"""Password hashing (bcrypt) and access tokens (JWT)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

PASSWORD_MIN_LENGTH = 8
BCRYPT_MAX_BYTES = 72  # bcrypt only looks at the first 72 bytes, so longer input is refused
JWT_ISSUER = "shoplite"


class PasswordPolicyError(ValueError):
    """The password does not meet the policy."""


class AuthError(Exception):
    """A token is missing, malformed, expired or signed with the wrong key."""


def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(f"password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(password.encode()) > BCRYPT_MAX_BYTES:
        raise PasswordPolicyError(f"password must be at most {BCRYPT_MAX_BYTES} bytes")
    if password != password.strip():
        raise PasswordPolicyError("password must not start or end with whitespace")
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        raise PasswordPolicyError("password must contain at least one letter and one digit")


def hash_password(password: str, rounds: int = 12) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:  # malformed hash, or input over bcrypt's 72-byte limit
        return False


def create_access_token(
    *,
    subject: str,
    role: str,
    secret: str,
    expires_minutes: int,
    algorithm: str = "HS256",
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role,
        "iss": JWT_ISSUER,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, secret: str, algorithm: str = "HS256") -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("token has expired") from None
    except jwt.InvalidTokenError:
        raise AuthError("invalid token") from None
