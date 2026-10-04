"""Request and response models (Pydantic v2). Amounts in the API are rupees with 2 decimals."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import Coupon, Order, Product
from app.services.money import format_inr, from_paise
from app.services.notifications import is_valid_email
from app.services.pricing import Quote
from app.services.reports import SalesReport
from app.services.tax import GST_RATES

Category = Literal[
    "essentials", "books", "apparel", "footwear", "electronics", "home", "beauty", "luxury"
]
OrderStatus = Literal["pending", "paid", "shipped", "delivered", "cancelled"]
MAX_PRICE = Decimal("1000000")


def _clean_email(value: str) -> str:
    value = value.strip().lower()
    if not is_valid_email(value):
        raise ValueError("not a valid e-mail address")
    return value


# --- users and auth -------------------------------------------------------------------------


class UserCreate(BaseModel):
    email: str = Field(max_length=254, examples=["asha@example.com"])
    full_name: str = Field(min_length=2, max_length=120, examples=["Asha Patil"])
    password: str = Field(min_length=8, max_length=128, examples=["Secure123pass"])

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _clean_email(value)

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("name is too short")
        return value


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return value.strip().lower()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: str
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


# --- products ---------------------------------------------------------------------------------


class ProductCreate(BaseModel):
    sku: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]{2,31}$", examples=["ELEC-EARBUDS-01"])
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    category: Category
    price: Decimal = Field(gt=0, le=MAX_PRICE, decimal_places=2, examples=["2499.00"])
    stock: int = Field(default=0, ge=0, le=1_000_000)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    category: Category | None = None
    price: Decimal | None = Field(default=None, gt=0, le=MAX_PRICE, decimal_places=2)
    stock: int | None = Field(default=None, ge=0, le=1_000_000)
    is_active: bool | None = None


class RestockRequest(BaseModel):
    quantity: int = Field(gt=0, le=100_000)


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    description: str
    category: str
    price: Decimal
    gst_rate: int
    stock: int
    is_active: bool

    @classmethod
    def from_model(cls, product: Product) -> ProductOut:
        return cls(
            id=product.id,
            sku=product.sku,
            name=product.name,
            description=product.description,
            category=product.category,
            price=from_paise(product.price_paise),
            gst_rate=GST_RATES[product.category],
            stock=product.stock,
            is_active=product.is_active,
        )


class ProductPage(BaseModel):
    items: list[ProductOut]
    total: int
    page: int
    page_size: int
    pages: int


# --- coupons ----------------------------------------------------------------------------------


class CouponCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9]{3,20}$", examples=["MONSOON15"])
    kind: Literal["percent", "flat"]
    value: Decimal = Field(gt=0, decimal_places=2, description="Percent (1-90) or rupees off")
    max_discount: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    min_order: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    expires_at: datetime | None = None
    usage_limit: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_value(self) -> CouponCreate:
        if self.kind == "percent" and (self.value > 90 or self.value != self.value.to_integral()):
            raise ValueError("percent coupons take a whole number from 1 to 90")
        if self.kind == "flat" and self.max_discount is not None:
            raise ValueError("max_discount only applies to percent coupons")
        return self


class CouponOut(BaseModel):
    code: str
    kind: str
    value: Decimal
    max_discount: Decimal | None
    min_order: Decimal
    expires_at: datetime | None
    usage_limit: int | None
    used_count: int
    is_active: bool

    @classmethod
    def from_model(cls, coupon: Coupon) -> CouponOut:
        value = Decimal(coupon.value) if coupon.kind == "percent" else from_paise(coupon.value)
        max_discount = (
            from_paise(coupon.max_discount_paise) if coupon.max_discount_paise is not None else None
        )
        return cls(
            code=coupon.code,
            kind=coupon.kind,
            value=value,
            max_discount=max_discount,
            min_order=from_paise(coupon.min_order_paise),
            expires_at=coupon.expires_at,
            usage_limit=coupon.usage_limit,
            used_count=coupon.used_count,
            is_active=coupon.is_active,
        )


# --- orders -----------------------------------------------------------------------------------


class OrderLineIn(BaseModel):
    product_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=1000)


class OrderCreate(BaseModel):
    items: list[OrderLineIn] = Field(min_length=1, max_length=50)
    shipping_state: str = Field(min_length=2, max_length=2, examples=["MH"])
    coupon_code: str | None = Field(default=None, max_length=20, examples=["WELCOME10"])


class AmountsOut(BaseModel):
    subtotal: Decimal
    discount: Decimal
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    tax: Decimal
    shipping: Decimal
    total: Decimal
    total_display: str

    @classmethod
    def build(
        cls,
        *,
        subtotal: int,
        discount: int,
        taxable: int,
        cgst: int,
        sgst: int,
        igst: int,
        shipping: int,
        total: int,
    ) -> AmountsOut:
        return cls(
            subtotal=from_paise(subtotal),
            discount=from_paise(discount),
            taxable=from_paise(taxable),
            cgst=from_paise(cgst),
            sgst=from_paise(sgst),
            igst=from_paise(igst),
            tax=from_paise(cgst + sgst + igst),
            shipping=from_paise(shipping),
            total=from_paise(total),
            total_display=format_inr(total),
        )


class QuoteLineOut(BaseModel):
    product_id: int
    quantity: int
    unit_price: Decimal
    bulk_discount_percent: int
    line_total: Decimal
    coupon_share: Decimal
    taxable: Decimal
    tax_rate: int
    tax: Decimal


class QuoteOut(BaseModel):
    lines: list[QuoteLineOut]
    amounts: AmountsOut

    @classmethod
    def from_quote(cls, quote: Quote) -> QuoteOut:
        lines = [
            QuoteLineOut(
                product_id=line.product_id,
                quantity=line.quantity,
                unit_price=from_paise(line.unit_price_paise),
                bulk_discount_percent=line.bulk_discount_percent,
                line_total=from_paise(line.line_total_paise),
                coupon_share=from_paise(line.coupon_share_paise),
                taxable=from_paise(line.taxable_paise),
                tax_rate=line.tax_rate,
                tax=from_paise(line.tax.total_paise),
            )
            for line in quote.lines
        ]
        amounts = AmountsOut.build(
            subtotal=quote.subtotal_paise,
            discount=quote.discount_paise,
            taxable=quote.taxable_paise,
            cgst=quote.cgst_paise,
            sgst=quote.sgst_paise,
            igst=quote.igst_paise,
            shipping=quote.shipping_paise,
            total=quote.total_paise,
        )
        return cls(lines=lines, amounts=amounts)


class OrderItemOut(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    tax_rate: int


class OrderOut(BaseModel):
    id: int
    status: str
    shipping_state: str
    coupon_code: str | None
    payment_ref: str | None
    items: list[OrderItemOut]
    amounts: AmountsOut
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, order: Order) -> OrderOut:
        items = [
            OrderItemOut(
                product_id=item.product_id,
                product_name=item.product.name,
                quantity=item.quantity,
                unit_price=from_paise(item.unit_price_paise),
                line_total=from_paise(item.line_total_paise),
                tax_rate=item.tax_rate,
            )
            for item in order.items
        ]
        amounts = AmountsOut.build(
            subtotal=order.subtotal_paise,
            discount=order.discount_paise,
            taxable=order.taxable_paise,
            cgst=order.cgst_paise,
            sgst=order.sgst_paise,
            igst=order.igst_paise,
            shipping=order.shipping_paise,
            total=order.total_paise,
        )
        return cls(
            id=order.id,
            status=order.status,
            shipping_state=order.shipping_state,
            coupon_code=order.coupon_code,
            payment_ref=order.payment_ref,
            items=items,
            amounts=amounts,
            created_at=order.created_at,
            updated_at=order.updated_at,
        )


class OrderActionOut(BaseModel):
    order: OrderOut
    refunded: bool = False


class PaymentRequest(BaseModel):
    card_token: str = Field(pattern=r"^tok_[a-z_]{2,40}$", examples=["tok_visa"])


# --- reports ----------------------------------------------------------------------------------


class CategorySalesOut(BaseModel):
    category: str
    revenue: Decimal
    units: int


class ProductSalesOut(BaseModel):
    product_id: int
    name: str
    units: int
    revenue: Decimal


class DailySalesOut(BaseModel):
    day: str
    orders: int
    revenue: Decimal


class SalesReportOut(BaseModel):
    orders_by_status: dict[str, int]
    order_count: int
    paid_order_count: int
    revenue: Decimal
    average_order_value: Decimal
    by_category: list[CategorySalesOut]
    top_products: list[ProductSalesOut]
    daily: list[DailySalesOut]

    @classmethod
    def from_report(cls, report: SalesReport) -> SalesReportOut:
        return cls(
            orders_by_status=report.orders_by_status,
            order_count=report.order_count,
            paid_order_count=report.paid_order_count,
            revenue=from_paise(report.revenue_paise),
            average_order_value=from_paise(report.average_order_value_paise),
            by_category=[
                CategorySalesOut(
                    category=row.category, revenue=from_paise(row.revenue_paise), units=row.units
                )
                for row in report.by_category
            ],
            top_products=[
                ProductSalesOut(
                    product_id=row.product_id,
                    name=row.name,
                    units=row.units,
                    revenue=from_paise(row.revenue_paise),
                )
                for row in report.top_products
            ],
            daily=[
                DailySalesOut(day=row.day, orders=row.orders, revenue=from_paise(row.revenue_paise))
                for row in report.daily
            ],
        )


# --- service info -----------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: str
    database: str


class VersionOut(BaseModel):
    app: str
    version: str
    git_sha: str
    build_time: str
    environment: str
