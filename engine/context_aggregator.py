"""
context_aggregator.py
---------------------
Enriches a RecoveryEvent with additional context needed by the AI Decision Engine:
  - Computes recovery priority score (heuristic pre-LLM filter)
  - Checks policy constraints (intervention budget, discount cooldown, etc.)
  - Generates a structured natural-language context block for the LLM prompt

This layer is deterministic and does NOT call the LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Optional

from .event_normalizer import RecoveryEvent


@dataclass
class PolicyViolation:
    rule: str
    reason: str


@dataclass
class EnrichedContext:
    event: RecoveryEvent

    # Derived heuristics
    recovery_priority: str          # HIGH | MEDIUM | LOW | SKIP
    estimated_recovery_probability_pct: float
    suggested_strategies: list[str] = field(default_factory=list)

    # Policy pre-checks
    policy_violations: list[PolicyViolation] = field(default_factory=list)
    discount_allowed: bool = True
    emi_allowed: bool = True
    promise_to_pay_allowed: bool = True
    downgrade_allowed: bool = True
    human_escalation_required: bool = False
    must_do_nothing: bool = False

    # Context block (string fed into LLM prompt)
    llm_context_block: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Heuristic priority & recoverability scoring
# ---------------------------------------------------------------------------

NEAR_UNRECOVERABLE_REASONS = {
    "card_stolen", "customer_fraud_risk", "customer_fraud",
}

HIGH_RECOVERY_REASONS = {
    "invalid_otp", "gateway_technical_error", "do_not_honor",
    "subscription_renewal_failed", "cart_abandoned", "invoice_overdue_b2b",
}


def _estimate_recovery_probability(event: RecoveryEvent) -> float:
    reason = event.failure.reason.lower()
    amount = event.payment.amount_inr
    prev_interventions = event.customer.previous_interventions_30d

    if event.failure.is_fraud_flag:
        return 2.0
    if event.failure.is_late_auth_risk:
        return 80.0
    if reason in HIGH_RECOVERY_REASONS:
        base = 65.0
    elif reason == "insufficient_funds":
        base = 40.0
    elif reason == "card_expired":
        base = 55.0
    elif reason in ("invoice_overdue_b2b_long",):
        base = 30.0
    else:
        base = 45.0

    if amount > 50000:
        base -= 10
    elif amount < 3000:
        base += 5

    base -= prev_interventions * 8
    return max(2.0, min(95.0, base))


def _priority_from_probability(prob: float, amount: float) -> str:
    if prob < 5:
        return "SKIP"
    if prob >= 60 and amount >= 5000:
        return "HIGH"
    if prob >= 40 or amount >= 15000:
        return "MEDIUM"
    return "LOW"


def _suggest_strategies(event: RecoveryEvent, prob: float) -> list[str]:
    reason = event.failure.reason.lower()
    amount = event.payment.amount_inr
    strategies = []

    if event.failure.is_fraud_flag or prob < 5:
        return ["do_nothing"]
    if event.failure.is_late_auth_risk:
        return ["do_nothing"]

    if reason == "invalid_otp":
        strategies.append("create_payment_link")
    elif reason == "gateway_technical_error":
        strategies.append("create_payment_link")
    elif reason == "insufficient_funds":
        if amount >= 3000:
            strategies.append("convert_to_emi")
        if amount < 10000:
            strategies.append("apply_discount")
        strategies.append("create_payment_link")
    elif reason == "card_expired":
        strategies.append("create_payment_link")
    elif reason == "do_not_honor":
        strategies.append("create_payment_link")
        if amount < 5000:
            strategies.append("apply_discount")
    elif reason == "subscription_renewal_failed":
        strategies.append("convert_to_emi")
        strategies.append("downgrade_subscription")
        strategies.append("apply_discount")
    elif reason == "cart_abandoned":
        strategies.append("create_payment_link")
        if amount > 5000:
            strategies.append("convert_to_emi")
        strategies.append("apply_discount")
    elif "invoice_overdue" in reason:
        strategies.append("schedule_promise_to_pay")
        if event.invoice and event.invoice.overdue_days > 14:
            strategies.append("human_escalate")
        else:
            strategies.append("create_payment_link")

    return strategies or ["create_payment_link"]


def _check_policy_constraints(event: RecoveryEvent, policy: dict) -> list[PolicyViolation]:
    violations = []
    customer = event.customer
    amount = event.payment.amount_inr

    budget = policy.get("intervention_budget", {})
    max_interventions = budget.get("max_interventions_per_customer_per_30d", 3)
    if customer.previous_interventions_30d >= max_interventions:
        violations.append(PolicyViolation(
            rule="intervention_budget",
            reason=f"Customer already has {customer.previous_interventions_30d} interventions in last 30 days (max={max_interventions})"
        ))

    disc_policy = policy.get("discount_policy", {})
    cooldown = disc_policy.get("discount_cooldown_days", 90)
    if customer.last_discount_days_ago is not None and customer.last_discount_days_ago < cooldown:
        violations.append(PolicyViolation(
            rule="discount_cooldown",
            reason=f"Customer received a discount {customer.last_discount_days_ago}d ago (cooldown={cooldown}d)"
        ))

    emi_policy = policy.get("emi_policy", {})
    min_emi_amount = emi_policy.get("min_order_value_inr", 3000)
    if amount < min_emi_amount:
        violations.append(PolicyViolation(
            rule="emi_minimum",
            reason=f"Order Rs{amount:.0f} below EMI minimum Rs{min_emi_amount:.0f}"
        ))

    escalation = policy.get("human_escalation", {})
    if amount > escalation.get("escalate_on_amount_above_inr", 50000):
        violations.append(PolicyViolation(
            rule="human_escalation_required",
            reason=f"Amount Rs{amount:,.0f} exceeds human-escalation threshold"
        ))

    if event.invoice:
        threshold = escalation.get("escalate_on_b2b_invoice_overdue_days", 14)
        if event.invoice.overdue_days > threshold:
            violations.append(PolicyViolation(
                rule="human_escalation_required",
                reason=f"B2B invoice overdue {event.invoice.overdue_days} days (threshold={threshold})"
            ))

    return violations


def _derive_permissions(violations: list[PolicyViolation], prob: float, policy: dict) -> dict:
    violation_rules = {v.rule for v in violations}
    stopping = policy.get("stopping_rules", {})

    must_do_nothing = (
        prob < stopping.get("stop_on_recovery_probability_below_pct", 5)
        or "intervention_budget" in violation_rules
    )

    discount_allowed = (
        not must_do_nothing
        and "discount_cooldown" not in violation_rules
        and policy.get("discount_policy", {}).get("enabled", True)
    )

    emi_allowed = (
        not must_do_nothing
        and "emi_minimum" not in violation_rules
        and policy.get("emi_policy", {}).get("allow_emi_conversion", True)
    )

    downgrade_allowed = (
        not must_do_nothing
        and policy.get("subscription_downgrade_policy", {}).get("allow_downgrade", True)
    )

    promise_allowed = (
        not must_do_nothing
        and policy.get("promise_to_pay_policy", {}).get("enabled", True)
    )

    human_required = "human_escalation_required" in violation_rules

    return {
        "must_do_nothing": must_do_nothing,
        "discount_allowed": discount_allowed,
        "emi_allowed": emi_allowed,
        "downgrade_allowed": downgrade_allowed,
        "promise_to_pay_allowed": promise_allowed,
        "human_escalation_required": human_required,
    }


def _build_llm_context(event: RecoveryEvent, enriched: EnrichedContext, policy: dict) -> str:
    evt = event
    lines = [
        "=== REVENUE RECOVERY CONTEXT ===",
        f"Event ID        : {evt.event_id}",
        f"Event Type      : {evt.event_type}",
        f"Timestamp       : {evt.timestamp}",
        "",
        "--- CUSTOMER ---",
        f"Name            : {evt.customer.name}",
        f"Email           : {evt.customer.email}",
        f"B2B Customer    : {evt.customer.is_b2b}",
        f"Prior Successes : {evt.customer.previous_successful_payments} successful payments",
        f"Interventions   : {evt.customer.previous_interventions_30d} in last 30 days",
        f"Last Discount   : {evt.customer.last_discount_days_ago or 'Never'} days ago",
        "",
        "--- PAYMENT ---",
        f"Amount          : Rs{evt.payment.amount_inr:,.2f}",
        f"Product         : {evt.payment.product_name}",
        f"Type            : {evt.payment.product_type}",
        f"Method          : {evt.payment.method}",
        "",
        "--- FAILURE ---",
        f"Error Code      : {evt.failure.code}",
        f"Source          : {evt.failure.source}",
        f"Step            : {evt.failure.step}",
        f"Reason          : {evt.failure.reason}",
        f"Description     : {evt.failure.description}",
        f"Fraud Flag      : {evt.failure.is_fraud_flag}",
        f"Late Auth Risk  : {evt.failure.is_late_auth_risk}",
    ]

    if evt.subscription:
        sub = evt.subscription
        lines += [
            "",
            "--- SUBSCRIPTION ---",
            f"Subscription ID : {sub.subscription_id}",
            f"Plan ID         : {sub.plan_id}",
            f"Progress        : {sub.paid_count}/{sub.total_count} payments made",
            f"Status          : {sub.status}",
        ]

    if evt.invoice:
        inv = evt.invoice
        lines += [
            "",
            "--- INVOICE ---",
            f"Invoice ID      : {inv.invoice_id}",
            f"Due Date        : {inv.due_date}",
            f"Overdue Days    : {inv.overdue_days}",
            f"Notes           : {inv.invoice_notes}",
        ]

    disc = policy.get("discount_policy", {})
    emi = policy.get("emi_policy", {})
    lines += [
        "",
        "--- POLICY BOUNDS ---",
        f"Max Discount    : {disc.get('max_discount_pct', 0)}% or Rs{disc.get('max_discount_absolute_inr', 0):,}",
        f"EMI Conversion  : {'Allowed' if enriched.emi_allowed else 'NOT ALLOWED'}",
        f"EMI Max Months  : {emi.get('max_emi_months', 3)}",
        f"Discount        : {'Allowed' if enriched.discount_allowed else 'NOT ALLOWED'}",
        f"Must Do Nothing : {enriched.must_do_nothing}",
        "",
        "--- HEURISTIC PRE-ANALYSIS ---",
        f"Recovery Priority   : {enriched.recovery_priority}",
        f"Recovery Probability: {enriched.estimated_recovery_probability_pct:.1f}%",
        f"Suggested Strategies: {', '.join(enriched.suggested_strategies)}",
        "",
        "=================================",
    ]
    return "\n".join(lines)


def enrich(event: RecoveryEvent, policy: dict) -> EnrichedContext:
    prob = _estimate_recovery_probability(event)
    priority = _priority_from_probability(prob, event.payment.amount_inr)
    strategies = _suggest_strategies(event, prob)
    violations = _check_policy_constraints(event, policy)
    perms = _derive_permissions(violations, prob, policy)

    ctx = EnrichedContext(
        event=event,
        recovery_priority=priority,
        estimated_recovery_probability_pct=prob,
        suggested_strategies=strategies,
        policy_violations=violations,
        discount_allowed=perms["discount_allowed"],
        emi_allowed=perms["emi_allowed"],
        promise_to_pay_allowed=perms["promise_to_pay_allowed"],
        downgrade_allowed=perms["downgrade_allowed"],
        human_escalation_required=perms["human_escalation_required"],
        must_do_nothing=perms["must_do_nothing"],
    )

    ctx.llm_context_block = _build_llm_context(event, ctx, policy)
    return ctx
