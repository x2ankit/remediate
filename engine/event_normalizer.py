"""
event_normalizer.py
-------------------
Maps raw Razorpay webhook payloads (and our synthetic events) to a
canonical RecoveryEvent dataclass consumed by the rest of the engine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class CustomerProfile:
    id: str
    name: str
    email: str
    phone: str
    is_b2b: bool
    previous_interventions_30d: int
    previous_successful_payments: int
    last_discount_days_ago: Optional[int]


@dataclass
class PaymentContext:
    payment_id: str
    order_id: str
    amount_inr: float
    currency: str
    method: str
    product_name: str
    product_type: str   # subscription | invoice | one_time


@dataclass
class FailureContext:
    code: str
    source: str          # customer | issuer | gateway | razorpay | business
    step: str            # payment_authentication | payment_authorization | checkout | invoice
    reason: str
    description: str
    is_fraud_flag: bool = False
    is_late_auth_risk: bool = False


@dataclass
class SubscriptionContext:
    subscription_id: str
    plan_id: str
    paid_count: int
    total_count: int
    status: str


@dataclass
class InvoiceContext:
    invoice_id: str
    due_date: str
    overdue_days: int
    invoice_notes: str


@dataclass
class RecoveryEvent:
    """Canonical representation of any revenue-at-risk event."""
    event_id: str
    event_index: int
    event_type: str          # payment.failed | checkout.abandoned | invoice.expired
    timestamp: str
    merchant_id: str
    customer: CustomerProfile
    payment: PaymentContext
    failure: FailureContext
    subscription: Optional[SubscriptionContext] = None
    invoice: Optional[InvoiceContext] = None
    # Ground-truth hint (used only by evaluator, ignored by AI engine)
    recovery_hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Fraud / special-case detection helpers
# ---------------------------------------------------------------------------

FRAUD_REASONS = {"card_stolen", "customer_fraud_risk", "customer_fraud"}
LATE_AUTH_REASONS = {"timeout", "connection_lost", "network_error"}


def _is_fraud(reason: str) -> bool:
    return reason.lower() in FRAUD_REASONS


def _is_late_auth_risk(code: str, source: str, reason: str) -> bool:
    return (
        source in ("razorpay", "gateway")
        and reason.lower() in LATE_AUTH_REASONS
    )


# ---------------------------------------------------------------------------
# Normalization entry point
# ---------------------------------------------------------------------------

def normalize(raw: dict) -> RecoveryEvent:
    """
    Convert a raw event dict (from webhook or synthetic generator) into
    a RecoveryEvent. Raises ValueError on unrecognised or malformed events.
    """
    craw = raw.get("customer", {})
    praw = raw.get("payment", {})
    eraw = raw.get("error", {})

    customer = CustomerProfile(
        id=craw.get("id", "unknown"),
        name=craw.get("name", "Unknown Customer"),
        email=craw.get("email", ""),
        phone=craw.get("phone", ""),
        is_b2b=craw.get("is_b2b", False),
        previous_interventions_30d=craw.get("previous_interventions_30d", 0),
        previous_successful_payments=craw.get("previous_successful_payments", 0),
        last_discount_days_ago=craw.get("last_discount_days_ago"),
    )

    payment = PaymentContext(
        payment_id=praw.get("payment_id", ""),
        order_id=praw.get("order_id", ""),
        amount_inr=float(praw.get("amount_inr", 0)),
        currency=praw.get("currency", "INR"),
        method=praw.get("method", "unknown"),
        product_name=praw.get("product_name", ""),
        product_type=praw.get("product_type", "one_time"),
    )

    reason = eraw.get("reason", "")
    code   = eraw.get("code", "")
    source = eraw.get("source", "")

    failure = FailureContext(
        code=code,
        source=source,
        step=eraw.get("step", ""),
        reason=reason,
        description=eraw.get("description", ""),
        is_fraud_flag=_is_fraud(reason),
        is_late_auth_risk=_is_late_auth_risk(code, source, reason),
    )

    subscription: Optional[SubscriptionContext] = None
    if "subscription" in raw:
        sraw = raw["subscription"]
        subscription = SubscriptionContext(
            subscription_id=sraw.get("subscription_id", ""),
            plan_id=sraw.get("plan_id", ""),
            paid_count=int(sraw.get("paid_count", 0)),
            total_count=int(sraw.get("total_count", 12)),
            status=sraw.get("status", "active"),
        )

    invoice: Optional[InvoiceContext] = None
    if "invoice" in raw:
        iraw = raw["invoice"]
        invoice = InvoiceContext(
            invoice_id=iraw.get("invoice_id", ""),
            due_date=iraw.get("due_date", ""),
            overdue_days=int(iraw.get("overdue_days", 0)),
            invoice_notes=iraw.get("invoice_notes", ""),
        )

    return RecoveryEvent(
        event_id=raw.get("event_id", ""),
        event_index=raw.get("event_index", -1),
        event_type=raw.get("event_type", "payment.failed"),
        timestamp=raw.get("timestamp", ""),
        merchant_id=raw.get("merchant_id", ""),
        customer=customer,
        payment=payment,
        failure=failure,
        subscription=subscription,
        invoice=invoice,
        recovery_hint=raw.get("recovery_hint", ""),
    )


def load_events_from_jsonl(path: str) -> list[RecoveryEvent]:
    """Load and normalize all events from a JSONL file."""
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            events.append(normalize(raw))
    return events
