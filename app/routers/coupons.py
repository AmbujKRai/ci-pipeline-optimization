"""Coupon management (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.clock import to_naive_utc
from app.deps import AdminUser, DbSession
from app.models import Coupon
from app.schemas import CouponCreate, CouponOut
from app.services.money import to_paise

router = APIRouter(prefix="/coupons", tags=["coupons"])


@router.post("", response_model=CouponOut, status_code=status.HTTP_201_CREATED)
def create_coupon(payload: CouponCreate, db: DbSession, _admin: AdminUser) -> CouponOut:
    if db.scalar(select(Coupon.id).where(Coupon.code == payload.code)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a coupon with this code already exists")
    coupon = Coupon(
        code=payload.code,
        kind=payload.kind,
        value=int(payload.value) if payload.kind == "percent" else to_paise(payload.value),
        max_discount_paise=to_paise(payload.max_discount) if payload.max_discount else None,
        min_order_paise=to_paise(payload.min_order),
        expires_at=to_naive_utc(payload.expires_at) if payload.expires_at else None,
        usage_limit=payload.usage_limit,
    )
    db.add(coupon)
    db.commit()
    db.refresh(coupon)
    return CouponOut.from_model(coupon)


@router.get("", response_model=list[CouponOut])
def list_coupons(db: DbSession, _admin: AdminUser) -> list[CouponOut]:
    coupons = db.scalars(select(Coupon).order_by(Coupon.code))
    return [CouponOut.from_model(coupon) for coupon in coupons]
