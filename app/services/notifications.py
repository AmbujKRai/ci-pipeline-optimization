"""E-mail notifications. The stub records messages instead of sending them."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.models import Order, User
from app.services.money import format_inr

EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def is_valid_email(address: str) -> bool:
    return bool(EMAIL_PATTERN.fullmatch(address)) and ".." not in address


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str
    message_id: str


class Notifier(Protocol):
    def send(self, to: str, subject: str, body: str) -> str: ...


class StubEmailNotifier:
    def __init__(self, latency_ms: int = 0, sleep: Callable[[float], None] = time.sleep) -> None:
        self.latency_ms = latency_ms
        self._sleep = sleep
        self.outbox: list[EmailMessage] = []

    def send(self, to: str, subject: str, body: str) -> str:
        if not is_valid_email(to):
            raise ValueError(f"invalid recipient: {to!r}")
        if not subject.strip():
            raise ValueError("subject cannot be empty")
        if self.latency_ms:
            self._sleep(self.latency_ms / 1000)
        digest = hashlib.sha256(f"{len(self.outbox)}:{to}:{subject}".encode()).hexdigest()
        message = EmailMessage(to=to, subject=subject, body=body, message_id=f"msg_{digest[:12]}")
        self.outbox.append(message)
        return message.message_id

    def sent_to(self, address: str) -> list[EmailMessage]:
        return [message for message in self.outbox if message.to == address]


def order_confirmation(order: Order, user: User) -> tuple[str, str]:
    lines = [
        f"Hi {user.full_name},",
        "",
        f"Thanks for your order #{order.id}. Here is your summary:",
        f"  Items:    {sum(item.quantity for item in order.items)}",
        f"  Subtotal: {format_inr(order.subtotal_paise)}",
        f"  Discount: {format_inr(order.discount_paise)}",
        f"  GST:      {format_inr(order.tax_paise)}",
        f"  Shipping: {format_inr(order.shipping_paise)}",
        f"  Total:    {format_inr(order.total_paise)}",
    ]
    return f"ShopLite order #{order.id} confirmed", "\n".join(lines)


def shipment_notice(order: Order, user: User) -> tuple[str, str]:
    body = f"Hi {user.full_name},\n\nGood news: order #{order.id} is on its way."
    return f"ShopLite order #{order.id} shipped", body


def cancellation_notice(order: Order, user: User, refunded: bool) -> tuple[str, str]:
    refund_line = (
        f"A refund of {format_inr(order.total_paise)} has been issued."
        if refunded
        else "No payment was taken."
    )
    body = f"Hi {user.full_name},\n\nOrder #{order.id} has been cancelled. {refund_line}"
    return f"ShopLite order #{order.id} cancelled", body
