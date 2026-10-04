"""Sales reports (admin only)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.deps import AdminUser, DbSession
from app.schemas import SalesReportOut
from app.services.reports import sales_report

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/sales", response_model=SalesReportOut)
def sales(
    db: DbSession,
    _admin: AdminUser,
    start: date | None = None,
    end: Annotated[date | None, Query(description="Inclusive end date")] = None,
    top: Annotated[int, Query(ge=1, le=50)] = 5,
) -> SalesReportOut:
    start_at = datetime.combine(start, time.min) if start else None
    end_at = datetime.combine(end, time.min) + timedelta(days=1) if end else None
    if start_at is not None and end_at is not None and start_at >= end_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "start must not be after end")
    return SalesReportOut.from_report(sales_report(db, start_at, end_at, top))
