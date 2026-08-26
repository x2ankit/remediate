"""
razorpay_mock.py
----------------
Mock layer that simulates Razorpay API responses for:
  - Payment Links (create, fetch)
  - Subscriptions (fetch, modify plan, pause, cancel)
  - EMI Conversion (simulate via payment link with instalment metadata)
  - Contacts / Notifications

All calls are logged. Configurable random failure rate to test idempotency.
"""

import json
import random
import time
import uuid
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("razorpay_mock")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [MOCK] %(levelname)s %(message)s")

# Set to >0 to simulate transient API errors (0.0–1.0)
SIMULATED_FAILURE_RATE = 0.05
_call_log: list[dict] = []


def _log_call(api: str, params: dict, response: dict, success: bool):
    _call_log.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "api": api,
        "params": params,
        "response": response,
        "success": success,
    })


def _maybe_fail(api: str):
    """Randomly raise a simulated transient error."""
    if random.random() < SIMULATED_FAILURE_RATE:
        raise RazorpayMockError(
            api, "GATEWAY_ERROR", f"Simulated transient timeout on {api}"
        )


class RazorpayMockError(Exception):
    def __init__(self, api: str, code: str, description: str):
        self.api = api
        self.code = code
        self.description = description
        super().__init__(f"[{code}] {description}")


# ---------------------------------------------------------------------------
# Payment Link API
# ---------------------------------------------------------------------------

_payment_links: dict[str, dict] = {}


def create_payment_link(
    amount_inr: float,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
    description: str,
    expire_by_hours: int = 48,
    idempotency_key: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Simulate POST /v1/payment_links"""
    _maybe_fail("create_payment_link")

    # Idempotency: return existing link if same key exists
    if idempotency_key and idempotency_key in _payment_links:
        existing = _payment_links[idempotency_key]
        logger.info(f"Idempotency hit — returning existing link {existing['id']}")
        return existing

    link_id = f"plink_{uuid.uuid4().hex[:14]}"
    short_url = f"https://rzp.io/l/{link_id[-6:]}"
    expire_at = datetime.now(timezone.utc) + timedelta(hours=expire_by_hours)

    link = {
        "id": link_id,
        "entity": "payment_link",
        "amount": int(amount_inr * 100),  # paise
        "currency": "INR",
        "accept_partial": False,
        "description": description,
        "customer": {
            "name": customer_name,
            "email": customer_email,
            "contact": customer_phone,
        },
        "short_url": short_url,
        "expire_by": int(expire_at.timestamp()),
        "status": "created",
        "payments": [],
        "metadata": metadata or {},
        "created_at": int(time.time()),
    }

    if idempotency_key:
        _payment_links[idempotency_key] = link
    _payment_links[link_id] = link

    _log_call("create_payment_link", {"amount_inr": amount_inr, "customer": customer_name}, link, True)
    logger.info(f"Created Payment Link {link_id} for ₹{amount_inr:.0f} → {short_url}")
    return link


def fetch_payment_link(link_id: str) -> dict:
    """Simulate GET /v1/payment_links/:id"""
    _maybe_fail("fetch_payment_link")
    link = _payment_links.get(link_id)
    if not link:
        err = {"error": "NOT_FOUND", "description": f"Payment link {link_id} not found"}
        _log_call("fetch_payment_link", {"link_id": link_id}, err, False)
        raise RazorpayMockError("fetch_payment_link", "NOT_FOUND", err["description"])
    return link


def simulate_payment_link_paid(link_id: str) -> dict:
    """Testing utility — mark a link as paid."""
    link = _payment_links.get(link_id)
    if link:
        link["status"] = "paid"
        link["payments"].append({
            "payment_id": f"pay_{uuid.uuid4().hex[:14]}",
            "amount": link["amount"],
            "paid_at": int(time.time()),
        })
    return link


# ---------------------------------------------------------------------------
# Subscription API
# ---------------------------------------------------------------------------

_subscriptions: dict[str, dict] = {}


def fetch_subscription(subscription_id: str) -> dict:
    """Simulate GET /v1/subscriptions/:id"""
    _maybe_fail("fetch_subscription")
    if subscription_id not in _subscriptions:
        # Bootstrap a mock subscription on first fetch
        _subscriptions[subscription_id] = {
            "id": subscription_id,
            "entity": "subscription",
            "status": "active",
            "plan_id": "plan_basic",
            "quantity": 1,
            "paid_count": random.randint(1, 11),
            "total_count": 12,
        }
    return _subscriptions[subscription_id]


def modify_subscription(
    subscription_id: str,
    new_plan_id: Optional[str] = None,
    quantity: Optional[int] = None,
    pause: bool = False,
    cancel: bool = False,
    pause_until: Optional[str] = None,
) -> dict:
    """Simulate PATCH /v1/subscriptions/:id"""
    _maybe_fail("modify_subscription")
    sub = fetch_subscription(subscription_id)

    if cancel:
        sub["status"] = "cancelled"
    elif pause:
        sub["status"] = "paused"
        sub["pause_until"] = pause_until
    if new_plan_id:
        sub["plan_id"] = new_plan_id
    if quantity is not None:
        sub["quantity"] = quantity

    params = {"subscription_id": subscription_id, "new_plan_id": new_plan_id,
               "pause": pause, "cancel": cancel}
    _log_call("modify_subscription", params, sub, True)
    logger.info(f"Modified subscription {subscription_id} → status={sub['status']}, plan={sub.get('plan_id')}")
    return sub


# ---------------------------------------------------------------------------
# EMI / Instalment API  (simulated via payment links with instalment metadata)
# ---------------------------------------------------------------------------

def create_emi_plan(
    total_amount_inr: float,
    emi_months: int,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
    description: str,
    idempotency_key: Optional[str] = None,
) -> list[dict]:
    """
    Simulate creating a series of Payment Links representing EMI instalments.
    Returns a list of payment link objects, one per instalment.
    """
    _maybe_fail("create_emi_plan")
    instalment_amount = round(total_amount_inr / emi_months, 2)
    links = []
    for month in range(1, emi_months + 1):
        due_date = datetime.now(timezone.utc) + timedelta(days=30 * (month - 1))
        emi_key = f"{idempotency_key}_emi_{month}" if idempotency_key else None
        link = create_payment_link(
            amount_inr=instalment_amount,
            customer_name=customer_name,
            customer_email=customer_email,
            customer_phone=customer_phone,
            description=f"{description} — Instalment {month}/{emi_months}",
            expire_by_hours=48 + 24 * (month - 1),
            idempotency_key=emi_key,
            metadata={"emi_month": month, "emi_total_months": emi_months, "full_amount_inr": total_amount_inr},
        )
        links.append(link)
    logger.info(f"Created EMI plan: {emi_months}x ₹{instalment_amount:.0f} for {customer_name}")
    return links


# ---------------------------------------------------------------------------
# Notification (stub — would call WhatsApp/SMS/email in real Razorpay)
# ---------------------------------------------------------------------------

_notifications: list[dict] = []


def send_notification(
    channel: str,
    recipient: str,
    message: str,
    payment_link_url: Optional[str] = None,
) -> dict:
    """Simulate sending a notification via WhatsApp/SMS/email."""
    notif = {
        "id": f"notif_{uuid.uuid4().hex[:10]}",
        "channel": channel,
        "recipient": recipient,
        "message": message,
        "payment_link_url": payment_link_url,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "status": "delivered",
    }
    _notifications.append(notif)
    logger.info(f"[{channel.upper()}] → {recipient}: {message[:80]}...")
    return notif


# ---------------------------------------------------------------------------
# CRM Ticket (human escalation stub)
# ---------------------------------------------------------------------------

_crm_tickets: list[dict] = []


def create_crm_ticket(
    customer_name: str,
    customer_email: str,
    amount_inr: float,
    reason: str,
    context_summary: str,
) -> dict:
    ticket = {
        "ticket_id": f"crm_{uuid.uuid4().hex[:10]}",
        "customer_name": customer_name,
        "customer_email": customer_email,
        "amount_inr": amount_inr,
        "reason": reason,
        "context_summary": context_summary,
        "priority": "HIGH" if amount_inr > 50000 else "MEDIUM",
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _crm_tickets.append(ticket)
    logger.info(f"CRM ticket created → {ticket['ticket_id']} for {customer_name} (₹{amount_inr:,.0f})")
    return ticket


# ---------------------------------------------------------------------------
# Audit dump utility
# ---------------------------------------------------------------------------

def dump_call_log(path: Optional[Path] = None) -> list[dict]:
    if path:
        with open(path, "w", encoding="utf-8") as f:
            for entry in _call_log:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return _call_log
