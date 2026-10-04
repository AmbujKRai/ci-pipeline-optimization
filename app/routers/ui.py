"""Server-rendered dashboard page."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app import __version__
from app.deps import DbSession, SettingsDep
from app.models import Product
from app.services import inventory
from app.services.money import format_inr
from app.services.reports import sales_report
from app.services.tax import GST_RATES

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["inr"] = format_inr

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: DbSession, settings: SettingsDep) -> HTMLResponse:
    active = Product.is_active.is_(True)
    catalogue = list(
        db.scalars(select(Product).where(active).order_by(Product.category, Product.name).limit(24))
    )
    context = {
        "settings": settings,
        "version": __version__,
        "short_sha": settings.git_sha[:7],
        "product_count": db.scalar(select(func.count(Product.id)).where(active)) or 0,
        "report": sales_report(db, top_n=5),
        "low_stock": inventory.low_stock(db, threshold=5)[:6],
        "catalogue": catalogue,
        "gst_rates": GST_RATES,
    }
    return templates.TemplateResponse(request, "index.html", context)
