"""Application factory."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app import __version__
from app.config import Settings
from app.database import Base, make_engine, make_session_factory
from app.routers import auth, coupons, health, orders, products, reports, ui
from app.security import PasswordPolicyError
from app.seed import seed_demo_data
from app.services.inventory import InactiveProductError, InsufficientStockError
from app.services.notifications import StubEmailNotifier
from app.services.orders import InvalidTransitionError, ProductNotFoundError
from app.services.payments import GatewayTimeoutError, PaymentDeclinedError, StubPaymentGateway
from app.services.pricing import CouponError
from app.services.tax import InvalidStateError, UnknownCategoryError

STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)

DESCRIPTION = """
ShopLite is a small inventory and order management service: catalogue, GST-aware pricing,
coupons, orders with a payment step, and sales reports.

It is the demo application of the *CI Pipeline Optimization Using Parallel Test Execution
and Build Caching* project.

**Try it:** register with `POST /auth/register`, log in with `POST /auth/login`, click
**Authorize** and paste the token, then place an order. Payments use test card tokens
such as `tok_visa` (approved) or `tok_declined`.
"""


def _error(status_code: int, detail: str, **extra: object) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, **extra})


def register_exception_handlers(app: FastAPI) -> None:
    """Translate domain errors into HTTP responses."""

    def not_found(_: Request, exc: Exception) -> JSONResponse:
        return _error(404, str(exc))

    def conflict(_: Request, exc: Exception) -> JSONResponse:
        return _error(409, str(exc))

    def coupon_rejected(_: Request, exc: Exception) -> JSONResponse:
        reason = exc.reason if isinstance(exc, CouponError) else "invalid"
        return _error(422, f"coupon rejected: {reason}", reason=reason)

    def payment_declined(_: Request, exc: Exception) -> JSONResponse:
        reason = exc.reason if isinstance(exc, PaymentDeclinedError) else "declined"
        return _error(402, f"payment declined: {reason}", reason=reason)

    def gateway_timeout(_: Request, exc: Exception) -> JSONResponse:
        return _error(504, "the payment gateway did not respond, please try again")

    def invalid_input(_: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, ValidationError):  # a bug in our own models, not bad input
            logger.exception("internal validation error", exc_info=exc)
            return _error(500, "internal error")
        return _error(422, str(exc))

    app.add_exception_handler(ProductNotFoundError, not_found)
    for conflict_error in (InactiveProductError, InsufficientStockError, InvalidTransitionError):
        app.add_exception_handler(conflict_error, conflict)
    app.add_exception_handler(CouponError, coupon_rejected)
    app.add_exception_handler(PaymentDeclinedError, payment_declined)
    app.add_exception_handler(GatewayTimeoutError, gateway_timeout)
    for input_error in (InvalidStateError, UnknownCategoryError, PasswordPolicyError, ValueError):
        app.add_exception_handler(input_error, invalid_input)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    if settings.seed_demo_data:
        with session_factory() as session:
            seed_demo_data(session, settings)

    app = FastAPI(
        title="ShopLite API",
        version=__version__,
        description=DESCRIPTION,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.payment_gateway = StubPaymentGateway(latency_ms=settings.payment_latency_ms)
    app.state.notifier = StubEmailNotifier(latency_ms=settings.notification_latency_ms)

    build_tag = f"{__version__}+{settings.git_sha[:7]}"

    @app.middleware("http")
    async def add_build_header(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-App-Version"] = build_tag
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for router in (
        health.router,
        auth.router,
        products.router,
        orders.router,
        coupons.router,
        reports.router,
        ui.router,
    ):
        app.include_router(router)
    register_exception_handlers(app)
    return app
