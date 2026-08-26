"""
remediation_executor.py
-----------------------
Executes tool-call decisions against the Razorpay Mock API.
Handles idempotency, exponential-backoff retries on transient errors,
and returns a structured RecoveryOutcome.
"""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import razorpay_mock as rzp
from .razorpay_mock import RazorpayMockError
from .decision_engine import DecisionResult
from .context_aggregator import EnrichedContext

logger = logging.getLogger("remediation_executor")

MAX_RETRIES = 3
RETRY_BACKOFF_S = [0.1, 0.3, 0.6]


@dataclass
class RecoveryOutcome:
    event_id: str
    tool_called: str
    tool_args: dict
    success: bool
    action_taken: str
    amount_targeted_inr: float
    amount_recovered_inr: float
    artifacts: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    fallback_used: bool = False
    retries_attempted: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _retry_api(fn, *args, **kwargs):
    last_err = None
    for attempt, backoff in enumerate(RETRY_BACKOFF_S):
        try:
            return fn(*args, **kwargs), attempt
        except RazorpayMockError as e:
            logger.warning(f"Transient error attempt {attempt + 1}: {e}. Retry in {backoff}s...")
            last_err = e
            time.sleep(backoff)
    raise last_err


def _exec_create_payment_link(args: dict, ctx: EnrichedContext) -> RecoveryOutcome:
    evt = ctx.event
    cust = evt.customer
    amount = float(args.get("amount_inr", evt.payment.amount_inr))
    idem_key = f"plink_{evt.event_id}"

    link, retries = _retry_api(
        rzp.create_payment_link,
        amount_inr=amount,
        customer_name=cust.name,
        customer_email=cust.email,
        customer_phone=cust.phone,
        description=f"Recovery: {evt.payment.product_name}",
        idempotency_key=idem_key,
    )
    notif, _ = _retry_api(
        rzp.send_notification,
        channel=args.get("channel", "whatsapp"),
        recipient=cust.email if args.get("channel") == "email" else cust.phone,
        message=args.get("message_to_customer", ""),
        payment_link_url=link["short_url"],
    )

    recovered = 0.0
    if random.random() < 0.55:
        rzp.simulate_payment_link_paid(link["id"])
        recovered = amount

    return RecoveryOutcome(
        event_id=evt.event_id, tool_called="create_payment_link", tool_args=args, success=True,
        action_taken=f"Payment link Rs{amount:,.0f} sent via {args.get('channel','whatsapp')}",
        amount_targeted_inr=amount, amount_recovered_inr=recovered,
        artifacts=[link, notif], retries_attempted=retries,
    )


def _exec_convert_to_emi(args: dict, ctx: EnrichedContext) -> RecoveryOutcome:
    evt = ctx.event
    cust = evt.customer
    amount = evt.payment.amount_inr
    months = int(args.get("emi_months", 3))
    idem_key = f"emi_{evt.event_id}"

    links, retries = _retry_api(
        rzp.create_emi_plan,
        total_amount_inr=amount, emi_months=months,
        customer_name=cust.name, customer_email=cust.email, customer_phone=cust.phone,
        description=f"EMI: {evt.payment.product_name}", idempotency_key=idem_key,
    )
    notif, _ = _retry_api(
        rzp.send_notification,
        channel=args.get("channel", "whatsapp"),
        recipient=cust.email if args.get("channel") == "email" else cust.phone,
        message=args.get("message_to_customer", ""),
        payment_link_url=links[0]["short_url"] if links else None,
    )

    recovered = 0.0
    if random.random() < 0.60:
        for lnk in links:
            rzp.simulate_payment_link_paid(lnk["id"])
        recovered = amount

    return RecoveryOutcome(
        event_id=evt.event_id, tool_called="convert_to_emi", tool_args=args, success=True,
        action_taken=f"EMI {months}x Rs{amount/months:,.0f} created",
        amount_targeted_inr=amount, amount_recovered_inr=recovered,
        artifacts=links + [notif], retries_attempted=retries,
    )


def _exec_apply_discount(args: dict, ctx: EnrichedContext) -> RecoveryOutcome:
    evt = ctx.event
    cust = evt.customer
    disc_pct = min(float(args.get("discount_pct", 10)), 15.0)
    orig_amount = evt.payment.amount_inr
    disc_amount = round(orig_amount * (1 - disc_pct / 100), 2)
    idem_key = f"disc_{evt.event_id}"

    link, retries = _retry_api(
        rzp.create_payment_link,
        amount_inr=disc_amount,
        customer_name=cust.name, customer_email=cust.email, customer_phone=cust.phone,
        description=f"Discount ({disc_pct:.0f}% off): {evt.payment.product_name}",
        idempotency_key=idem_key,
        metadata={"discount_pct": disc_pct, "original_amount_inr": orig_amount},
    )
    notif, _ = _retry_api(
        rzp.send_notification,
        channel=args.get("channel", "whatsapp"),
        recipient=cust.email if args.get("channel") == "email" else cust.phone,
        message=args.get("message_to_customer", ""),
        payment_link_url=link["short_url"],
    )

    recovered = 0.0
    if random.random() < 0.65:
        rzp.simulate_payment_link_paid(link["id"])
        recovered = disc_amount

    return RecoveryOutcome(
        event_id=evt.event_id, tool_called="apply_discount", tool_args=args, success=True,
        action_taken=f"{disc_pct:.0f}% discount -> Rs{disc_amount:,.2f} link sent",
        amount_targeted_inr=orig_amount, amount_recovered_inr=recovered,
        artifacts=[link, notif], retries_attempted=retries,
    )


def _exec_schedule_promise_to_pay(args: dict, ctx: EnrichedContext) -> RecoveryOutcome:
    evt = ctx.event
    cust = evt.customer
    promised_date = args.get("promised_payment_date", "")

    notif, retries = _retry_api(
        rzp.send_notification,
        channel=args.get("channel", "email"),
        recipient=cust.email if args.get("channel") == "email" else cust.phone,
        message=args.get("message_to_customer", ""),
    )

    scheduled = {
        "type": "promise_to_pay_reminder",
        "customer_id": cust.id,
        "amount_inr": evt.payment.amount_inr,
        "promised_date": promised_date,
        "status": "scheduled",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    recovered = evt.payment.amount_inr if random.random() < 0.50 else 0.0

    return RecoveryOutcome(
        event_id=evt.event_id, tool_called="schedule_promise_to_pay", tool_args=args, success=True,
        action_taken=f"PTP logged for {promised_date}; retries paused",
        amount_targeted_inr=evt.payment.amount_inr, amount_recovered_inr=recovered,
        artifacts=[notif, scheduled], retries_attempted=retries,
    )


def _exec_downgrade_subscription(args: dict, ctx: EnrichedContext) -> RecoveryOutcome:
    evt = ctx.event
    cust = evt.customer

    if not evt.subscription:
        raise ValueError("downgrade_subscription called but no subscription context")

    sub_id = evt.subscription.subscription_id
    action = args.get("action", "downgrade_plan")

    if action == "pause":
        pause_months = int(args.get("pause_months", 1))
        pause_until = (datetime.now(timezone.utc) + timedelta(days=30 * pause_months)).date().isoformat()
        sub, retries = _retry_api(rzp.modify_subscription, subscription_id=sub_id, pause=True, pause_until=pause_until)
        action_taken = f"Subscription {sub_id} paused until {pause_until}"
    else:
        sub, retries = _retry_api(rzp.modify_subscription, subscription_id=sub_id,
                                   new_plan_id=args.get("target_plan_id", "plan_basic"))
        action_taken = f"Subscription {sub_id} downgraded to {args.get('target_plan_id', 'plan_basic')}"

    notif, _ = _retry_api(
        rzp.send_notification,
        channel=args.get("channel", "email"),
        recipient=cust.email if args.get("channel") == "email" else cust.phone,
        message=args.get("message_to_customer", ""),
    )

    recovered = evt.payment.amount_inr * 0.40 if random.random() < 0.75 else 0.0

    return RecoveryOutcome(
        event_id=evt.event_id, tool_called="downgrade_subscription", tool_args=args, success=True,
        action_taken=action_taken,
        amount_targeted_inr=evt.payment.amount_inr, amount_recovered_inr=recovered,
        artifacts=[sub, notif], retries_attempted=retries,
    )


def _exec_do_nothing(args: dict, ctx: EnrichedContext) -> RecoveryOutcome:
    evt = ctx.event
    reason_cat = args.get("reason_category", "no_compliant_action_available")
    logger.info(f"[{evt.event_id}] DO NOTHING -> {reason_cat}")
    return RecoveryOutcome(
        event_id=evt.event_id, tool_called="do_nothing", tool_args=args, success=True,
        action_taken=f"No action — {reason_cat}",
        amount_targeted_inr=evt.payment.amount_inr, amount_recovered_inr=0.0,
    )


TOOL_EXECUTORS = {
    "create_payment_link": _exec_create_payment_link,
    "convert_to_emi": _exec_convert_to_emi,
    "apply_discount": _exec_apply_discount,
    "schedule_promise_to_pay": _exec_schedule_promise_to_pay,
    "downgrade_subscription": _exec_downgrade_subscription,
    "do_nothing": _exec_do_nothing,
}


def execute(decision: DecisionResult, ctx: EnrichedContext) -> RecoveryOutcome:
    tool = decision.tool_called
    executor = TOOL_EXECUTORS.get(tool)

    if not executor:
        return RecoveryOutcome(
            event_id=decision.event_id, tool_called=tool, tool_args=decision.tool_args,
            success=False, action_taken="Unknown tool",
            amount_targeted_inr=ctx.event.payment.amount_inr, amount_recovered_inr=0.0,
            error=f"Unknown tool: {tool}",
        )

    try:
        outcome = executor(decision.tool_args, ctx)
        outcome.fallback_used = decision.fallback_used
        return outcome
    except RazorpayMockError as e:
        logger.error(f"[{decision.event_id}] API error: {e}")
        return RecoveryOutcome(
            event_id=decision.event_id, tool_called=tool, tool_args=decision.tool_args,
            success=False, action_taken=f"API error: {e.description}",
            amount_targeted_inr=ctx.event.payment.amount_inr, amount_recovered_inr=0.0,
            error=str(e),
        )
    except Exception as e:
        logger.exception(f"[{decision.event_id}] Unexpected error: {e}")
        return RecoveryOutcome(
            event_id=decision.event_id, tool_called=tool, tool_args=decision.tool_args,
            success=False, action_taken=f"Error: {e}",
            amount_targeted_inr=ctx.event.payment.amount_inr, amount_recovered_inr=0.0,
            error=str(e),
        )
