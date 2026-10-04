"""Demo data so a fresh deployment has a catalogue, coupons and some order history."""

from __future__ import annotations

import logging
import random
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import Coupon, Product, User
from app.security import hash_password
from app.services import orders as order_service
from app.services.money import to_paise
from app.services.payments import StubPaymentGateway

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

# Marker for accounts that cannot log in (bcrypt rejects it as a hash).
UNUSABLE_HASH = "!disabled"

# (sku, name, category, price in rupees, stock, description)
CATALOGUE: tuple[tuple[str, str, str, str, int, str], ...] = (
    ("ESS-RICE-5KG", "Basmati Rice 5 kg", "essentials", "649.00", 120, "Aged long-grain rice."),
    ("ESS-OIL-1L", "Cold-Pressed Groundnut Oil 1 L", "essentials", "289.00", 80, "Wood-pressed."),
    ("ESS-DAL-1KG", "Organic Toor Dal 1 kg", "essentials", "189.00", 150, "Unpolished pulses."),
    ("ESS-CHAI-500", "Masala Chai 500 g", "essentials", "245.00", 4, "Assam tea with spices."),
    ("BK-PRAGPROG", "The Pragmatic Programmer", "books", "699.00", 40, "20th anniversary ed."),
    ("BK-DDIA", "Designing Data-Intensive Applications", "books", "1199.00", 25, "Hardcover."),
    ("BK-K8S-UR", "Kubernetes: Up and Running", "books", "1050.00", 3, "Third edition."),
    ("BK-HABITS", "Atomic Habits", "books", "399.00", 90, "Paperback."),
    ("APP-KURTA-M", "Cotton Kurta (M)", "apparel", "899.00", 60, "Hand-block printed."),
    ("APP-DENIM-L", "Denim Jacket (L)", "apparel", "1799.00", 30, "Stonewashed."),
    ("APP-TEE-M", "Graphic T-shirt (M)", "apparel", "499.00", 2, "100% cotton."),
    ("APP-SHIRT-40", "Formal Shirt (40)", "apparel", "1199.00", 45, "Wrinkle-free."),
    ("FW-RUN-9", "Running Shoes (UK 9)", "footwear", "2999.00", 35, "Cushioned midsole."),
    ("FW-CANVAS-8", "Canvas Sneakers (UK 8)", "footwear", "1299.00", 50, "Classic low-top."),
    ("FW-SANDAL-9", "Leather Sandals (UK 9)", "footwear", "999.00", 40, "Kolhapuri style."),
    ("EL-EARBUDS", "Wireless Earbuds", "electronics", "2499.00", 70, "30 h battery."),
    ("EL-KEYBOARD", "Mechanical Keyboard", "electronics", "4299.00", 25, "Hot-swap switches."),
    ("EL-USBC-HUB", "USB-C Hub 7-in-1", "electronics", "1899.00", 55, "HDMI, SD, PD 100 W."),
    ("EL-MON-4K27", "27-inch 4K Monitor", "electronics", "24999.00", 12, "IPS, 60 Hz."),
    ("EL-WATCH", "Smartwatch", "electronics", "5499.00", 1, "AMOLED, GPS."),
    ("EL-POWERBANK", "Power Bank 20000 mAh", "electronics", "1599.00", 65, "22.5 W fast charge."),
    ("HM-BOTTLE", "Steel Water Bottle 1 L", "home", "549.00", 100, "Insulated, 24 h cold."),
    ("HM-DINNER", "Ceramic Dinner Set", "home", "2199.00", 20, "18 pieces."),
    ("HM-LAMP", "LED Desk Lamp", "home", "1299.00", 35, "Three colour modes."),
    ("HM-COOKER-5L", "Pressure Cooker 5 L", "home", "1899.00", 28, "Stainless steel."),
    ("BT-SPF50", "Sunscreen SPF 50", "beauty", "399.00", 85, "Matte finish."),
    ("BT-FACEWASH", "Herbal Face Wash", "beauty", "249.00", 95, "Neem and tulsi."),
    ("BT-BEARDKIT", "Beard Grooming Kit", "beauty", "899.00", 22, "Oil, balm and comb."),
    ("LX-WATCH-AUTO", "Swiss Automatic Watch", "luxury", "89999.00", 5, "Sapphire crystal."),
    ("LX-SAREE", "Banarasi Silk Saree", "luxury", "15999.00", 8, "Handwoven zari."),
    ("LX-BRIEFCASE", "Leather Briefcase", "luxury", "12499.00", 10, "Full-grain leather."),
)

DEMO_CUSTOMERS = (
    ("asha.patil@example.com", "Asha Patil"),
    ("rohan.mehta@example.com", "Rohan Mehta"),
    ("fatima.shaikh@example.com", "Fatima Shaikh"),
    ("vikram.iyer@example.com", "Vikram Iyer"),
)
DEMO_STATES = ("MH", "MH", "MH", "KA", "DL", "GJ", "TN", "WB", "UP", "TG")


def _seed_coupons(session: Session) -> None:
    now = utcnow()
    session.add_all(
        [
            Coupon(
                code="WELCOME10",
                kind="percent",
                value=10,
                max_discount_paise=to_paise("200"),
                min_order_paise=0,
            ),
            Coupon(
                code="FLAT200", kind="flat", value=to_paise("200"), min_order_paise=to_paise("1500")
            ),
            Coupon(
                code="FESTIVE25",
                kind="percent",
                value=25,
                min_order_paise=0,
                expires_at=now - timedelta(days=1),
            ),
        ]
    )


def _seed_orders(session: Session, customers: list[User], products: list[Product]) -> None:
    """Thirty orders over the last two weeks in a realistic mix of states."""
    # A fixed seed keeps demo data identical across restarts; nothing here is security-related.
    rng = random.Random(2026)  # nosec B311
    gateway = StubPaymentGateway()
    now = utcnow()
    outcomes = (
        ["delivered"] * 10 + ["shipped"] * 6 + ["paid"] * 6 + ["pending"] * 5 + ["cancelled"] * 3
    )
    rng.shuffle(outcomes)
    stocked = [product for product in products if product.stock >= 10]
    for index, outcome in enumerate(outcomes):
        picks = rng.sample(stocked, k=rng.randint(1, 3))
        lines = [order_service.OrderLineRequest(p.id, rng.randint(1, 2)) for p in picks]
        created = now - timedelta(days=rng.randint(0, 13), hours=rng.randint(0, 23))
        order = order_service.create_order(
            session,
            rng.choice(customers),
            lines,
            rng.choice(DEMO_STATES),
            coupon_code="WELCOME10" if index % 7 == 0 else None,
            now=created,
        )
        if outcome in {"paid", "shipped", "delivered"}:
            order_service.pay_order(session, order, gateway, "tok_visa")
        if outcome in {"shipped", "delivered"}:
            order_service.ship_order(session, order)
        if outcome == "delivered":
            order_service.deliver_order(session, order)
        if outcome == "cancelled":
            order_service.cancel_order(session, order, gateway)


def seed_demo_data(session: Session, settings: Settings) -> bool:
    """Fill an empty database with demo data. Returns False if data already exists."""
    if session.scalar(select(func.count(User.id))):
        return False

    # Without ADMIN_PASSWORD the admin gets a random password nobody knows.
    admin_password = settings.admin_password or secrets.token_urlsafe(24)
    session.add(
        User(
            email=settings.admin_email,
            full_name="ShopLite Admin",
            password_hash=hash_password(admin_password, settings.bcrypt_rounds),
            role="admin",
        )
    )
    customers = [
        User(email=email, full_name=name, password_hash=UNUSABLE_HASH, role="customer")
        for email, name in DEMO_CUSTOMERS
    ]
    session.add_all(customers)
    products = [
        Product(
            sku=sku,
            name=name,
            category=category,
            price_paise=to_paise(price),
            stock=stock,
            description=description,
        )
        for sku, name, category, price, stock, description in CATALOGUE
    ]
    session.add_all(products)
    _seed_coupons(session)
    session.commit()

    _seed_orders(session, customers, products)
    logger.info("seeded demo data: %d products, %d customers", len(products), len(customers))
    return True
