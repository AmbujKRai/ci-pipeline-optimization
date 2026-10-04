"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else int(value)


@dataclass(frozen=True)
class Settings:
    """Application settings. Every field can be overridden with an environment variable."""

    app_name: str = "ShopLite"
    environment: str = "development"
    database_url: str = "sqlite:///./shoplite.db"
    # A fresh random secret per process unless JWT_SECRET is provided.
    jwt_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60
    bcrypt_rounds: int = 12
    git_sha: str = "local"
    build_time: str = "unknown"
    seed_demo_data: bool = True
    admin_email: str = "admin@shoplite.local"
    admin_password: str | None = None
    payment_latency_ms: int = 0
    notification_latency_ms: int = 0
    repo_url: str = "https://github.com/AmbujKRai/ci-pipeline-optimization"
    dashboard_url: str = "https://ambujkrai.github.io/ci-pipeline-optimization/"

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        return cls(
            environment=os.getenv("APP_ENV", defaults.environment),
            database_url=os.getenv("DATABASE_URL", defaults.database_url),
            jwt_secret=os.getenv("JWT_SECRET") or defaults.jwt_secret,
            jwt_expiry_minutes=_env_int("JWT_EXPIRY_MINUTES", defaults.jwt_expiry_minutes),
            bcrypt_rounds=_env_int("BCRYPT_ROUNDS", defaults.bcrypt_rounds),
            git_sha=os.getenv("GIT_SHA", defaults.git_sha),
            build_time=os.getenv("BUILD_TIME", defaults.build_time),
            seed_demo_data=_env_bool("SEED_DEMO_DATA", defaults.seed_demo_data),
            admin_email=os.getenv("ADMIN_EMAIL", defaults.admin_email),
            admin_password=os.getenv("ADMIN_PASSWORD") or None,
            payment_latency_ms=_env_int("PAYMENT_LATENCY_MS", defaults.payment_latency_ms),
            notification_latency_ms=_env_int(
                "NOTIFICATION_LATENCY_MS", defaults.notification_latency_ms
            ),
            repo_url=os.getenv("REPO_URL", defaults.repo_url),
            dashboard_url=os.getenv("DASHBOARD_URL", defaults.dashboard_url),
        )
