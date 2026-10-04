"""Shared fixtures. Every test gets its own app with a fresh in-memory database."""

from __future__ import annotations

import itertools
import os
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import Coupon, Product, User
from app.security import hash_password

# Test-only credential for users created inside the throwaway test databases.
TEST_PASSWORD = "Passw0rd-test"

# Simulated network latency for external-service tests can be scaled, e.g.
# TEST_LATENCY_SCALE=0 makes them instant, 2 doubles the round-trip time.
LATENCY_SCALE = float(os.getenv("TEST_LATENCY_SCALE", "1.0"))


def scaled_ms(milliseconds: int) -> int:
    return int(milliseconds * LATENCY_SCALE)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": "sqlite://",
        "jwt_secret": "test-secret-key-that-is-long-enough-for-hs256",
        "bcrypt_rounds": 4,
        "seed_demo_data": False,
        "git_sha": "0123456789abcdef",
        "build_time": "2026-01-01T00:00:00Z",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> Iterator:
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(app) -> Iterator[Session]:
    session: Session = app.state.session_factory()
    yield session
    session.close()


@pytest.fixture
def make_product(db: Session) -> Callable[..., Product]:
    counter = itertools.count(1)

    def _make(**overrides: object) -> Product:
        number = next(counter)
        data: dict[str, object] = {
            "sku": f"TEST-{number:04d}",
            "name": f"Test product {number}",
            "category": "electronics",
            "price_paise": 100_000,
            "stock": 50,
            "description": "",
        }
        data.update(overrides)
        product = Product(**data)
        db.add(product)
        db.commit()
        db.refresh(product)
        return product

    return _make


@pytest.fixture
def make_user(db: Session, settings: Settings) -> Callable[..., User]:
    counter = itertools.count(1)
    password_hash = hash_password(TEST_PASSWORD, settings.bcrypt_rounds)

    def _make(
        role: str = "customer", email: str | None = None, full_name: str = "Test User"
    ) -> User:
        user = User(
            email=email or f"user{next(counter)}@example.com",
            full_name=full_name,
            password_hash=password_hash,
            role=role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _make


@pytest.fixture
def make_coupon(db: Session) -> Callable[..., Coupon]:
    def _make(**overrides: object) -> Coupon:
        data: dict[str, object] = {
            "code": "SAVE10",
            "kind": "percent",
            "value": 10,
            "max_discount_paise": None,
            "min_order_paise": 0,
            "expires_at": None,
            "usage_limit": None,
            "used_count": 0,
            "is_active": True,
        }
        data.update(overrides)
        coupon = Coupon(**data)
        db.add(coupon)
        db.commit()
        db.refresh(coupon)
        return coupon

    return _make


def login(client: TestClient, email: str, password: str = TEST_PASSWORD) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def auth_headers(client: TestClient, make_user) -> Callable[..., dict[str, str]]:
    def _headers(role: str = "customer", email: str | None = None) -> dict[str, str]:
        user = make_user(role=role, email=email)
        return login(client, user.email)

    return _headers


@pytest.fixture
def customer_headers(auth_headers) -> dict[str, str]:
    return auth_headers("customer")


@pytest.fixture
def admin_headers(auth_headers) -> dict[str, str]:
    return auth_headers("admin")
