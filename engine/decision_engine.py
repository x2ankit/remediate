"""
decision_engine.py
------------------
AI Decision Engine — the LLM core of the Financial Remediation Engine.
Uses Google Gemini with structured function/tool calling.
Falls back to a deterministic heuristic engine when no API key is available.

Requires: GEMINI_API_KEY environment variable (optional — heuristic mode runs without it).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .context_aggregator import EnrichedContext

logger = logging.getLogger("decision_engine")

# ---------------------------------------------------------------------------
# Tool schemas (passed to Gemini function calling)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "create_payment_link",
        "description": (
            "Create a personalized Razorpay Payment Link and notify the customer. "
            "Use when the customer needs a new/fresh link to complete payment."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "amount_inr": {"type": "number", "description": "Amount in INR."},
                "message_to_customer": {"type": "string", "description": "Personalized empathetic message. Max 300 chars."},
                "channel": {"type": "string", "enum": ["whatsapp", "email", "sms"]},
                "rationale": {"type": "string", "description": "Internal reasoning for this choice."}
            },
            "required": ["amount_inr", "message_to_customer", "channel", "rationale"]
        }
    },
    {
        "name": "convert_to_emi",
        "description": (
            "Restructure the payment into equal monthly instalments. "
            "Only when emi_allowed=true and amount >= EMI minimum."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "emi_months": {"type": "integer", "description": "Number of monthly instalments (2, 3, or 6)."},
                "message_to_customer": {"type": "string"},
                "channel": {"type": "string", "enum": ["whatsapp", "email", "sms"]},
                "rationale": {"type": "string"}
            },
            "required": ["emi_months", "message_to_customer", "channel", "rationale"]
        }
    },
    {
        "name": "apply_discount",
        "description": (
            "Apply a discount and create a discounted Payment Link. "
            "Only when discount_allowed=true. discount_pct MUST NOT exceed policy max."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "discount_pct": {"type": "number", "description": "Percentage discount. Must be <= policy max_discount_pct."},
                "discount_type": {"type": "string", "enum": ["one_time", "temporary_recurring"]},
                "message_to_customer": {"type": "string"},
                "channel": {"type": "string", "enum": ["whatsapp", "email", "sms"]},
                "rationale": {"type": "string"}
            },
            "required": ["discount_pct", "discount_type", "message_to_customer", "channel", "rationale"]
        }
    },
    {
        "name": "schedule_promise_to_pay",
        "description": (
            "Handle a customer's promise to pay at a future date. "
            "Pauses retries and schedules a payment link for the promised date. "
            "Best for B2B invoice recovery."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "promised_payment_date": {"type": "string", "description": "ISO date string (YYYY-MM-DD)."},
                "deferral_reason": {"type": "string"},
                "message_to_customer": {"type": "string"},
                "channel": {"type": "string", "enum": ["whatsapp", "email", "sms"]},
                "rationale": {"type": "string"}
            },
            "required": ["promised_payment_date", "deferral_reason", "message_to_customer", "channel", "rationale"]
        }
    },
    {
        "name": "downgrade_subscription",
        "description": (
            "Switch the subscription to a lower-tier plan or pause it. "
            "Only when downgrade_allowed=true."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["downgrade_plan", "pause"]},
                "target_plan_id": {"type": "string"},
                "pause_months": {"type": "integer"},
                "message_to_customer": {"type": "string"},
                "channel": {"type": "string", "enum": ["whatsapp", "email", "sms"]},
                "rationale": {"type": "string"}
            },
            "required": ["action", "message_to_customer", "channel", "rationale"]
        }
    },
    {
        "name": "do_nothing",
        "description": (
            "Explicitly decide to take NO action. A first-class, audited decision. "
            "Use for: fraud flags, late-auth risk, budget exceeded, probability too low."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason_category": {
                    "type": "string",
                    "enum": [
                        "fraud_flag", "late_authorization_risk",
                        "intervention_budget_exceeded", "recovery_probability_too_low",
                        "no_compliant_action_available", "policy_violation", "permanent_failure"
                    ]
                },
                "rationale": {"type": "string", "description": "Detailed audit explanation."}
            },
            "required": ["reason_category", "rationale"]
        }
    }
]

SYSTEM_PROMPT = """You are the AI Financial Remediation Engine for Acme SaaS Solutions.

Your role is to analyze failed or at-risk revenue events and decide the single BEST
policy-compliant action to recover that revenue.

CRITICAL RULES:
1. You MUST call exactly ONE tool per event.
2. Respect ALL policy bounds shown in the context.
3. If must_do_nothing=True, call do_nothing immediately.
4. If fraud_flag=True, call do_nothing with reason_category=fraud_flag.
5. If late_auth_risk=True, call do_nothing with reason_category=late_authorization_risk.
6. Never hallucinate statistics. Never exceed policy limits.
7. discount_pct MUST NEVER exceed the policy max_discount_pct.
8. For B2B invoices, prefer schedule_promise_to_pay.
9. For subscription + insufficient_funds, prefer convert_to_emi or downgrade_subscription.
"""


@dataclass
class DecisionResult:
    event_id: str
    tool_called: str
    tool_args: dict
    raw_llm_response: str = ""
    error: Optional[str] = None
    fallback_used: bool = False


class DecisionEngine:
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model
        self._client = None

        if self.api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                self._client = genai
                logger.info(f"Gemini client initialized — model={model}")
            except ImportError:
                logger.warning("google-generativeai not installed. Running in heuristic mode.")
        else:
            logger.warning("No GEMINI_API_KEY found. Running in heuristic mode.")

    def decide(self, enriched: EnrichedContext) -> DecisionResult:
        event_id = enriched.event.event_id

        # Fast-path deterministic overrides
        if enriched.event.failure.is_fraud_flag:
            return DecisionResult(
                event_id=event_id, tool_called="do_nothing",
                tool_args={"reason_category": "fraud_flag",
                           "rationale": f"Fraud flag: reason={enriched.event.failure.reason}. Stopping per policy."}
            )

        if enriched.event.failure.is_late_auth_risk:
            return DecisionResult(
                event_id=event_id, tool_called="do_nothing",
                tool_args={"reason_category": "late_authorization_risk",
                           "rationale": "Razorpay-side timeout — payment may self-authorize. No duplicate recovery."}
            )

        if enriched.must_do_nothing:
            reason_cat = "intervention_budget_exceeded" if any(
                v.rule == "intervention_budget" for v in enriched.policy_violations
            ) else "recovery_probability_too_low"
            return DecisionResult(
                event_id=event_id, tool_called="do_nothing",
                tool_args={"reason_category": reason_cat,
                           "rationale": f"Stopping rule triggered. Probability={enriched.estimated_recovery_probability_pct:.1f}%."}
            )

        if self._client:
            return self._llm_decide(enriched)
        return self._heuristic_decide(enriched)

    def _llm_decide(self, enriched: EnrichedContext) -> DecisionResult:
        import google.generativeai as genai
        from google.generativeai.types import FunctionDeclaration, Tool

        event_id = enriched.event.event_id
        tools = [FunctionDeclaration(name=s["name"], description=s["description"],
                                      parameters=s["parameters"]) for s in TOOL_SCHEMAS]
        gemini_tool = Tool(function_declarations=tools)

        model = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=SYSTEM_PROMPT,
            tools=[gemini_tool],
        )

        try:
            response = model.generate_content(
                enriched.llm_context_block,
                tool_config={"function_calling_config": {"mode": "ANY"}},
            )
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    fc = part.function_call
                    args = dict(fc.args)
                    logger.info(f"[{event_id}] LLM -> {fc.name}")
                    return DecisionResult(event_id=event_id, tool_called=fc.name,
                                          tool_args=args, raw_llm_response=str(response))

            logger.warning(f"[{event_id}] No function call from LLM. Falling back.")
            result = self._heuristic_decide(enriched)
            result.fallback_used = True
            return result

        except Exception as e:
            logger.error(f"[{event_id}] LLM error: {e}. Falling back.")
            result = self._heuristic_decide(enriched)
            result.fallback_used = True
            result.error = str(e)
            return result

    def _heuristic_decide(self, enriched: EnrichedContext) -> DecisionResult:
        event = enriched.event
        event_id = event.event_id
        strategies = enriched.suggested_strategies
        amount = event.payment.amount_inr
        customer = event.customer

        if not strategies or strategies[0] == "do_nothing":
            return DecisionResult(
                event_id=event_id, tool_called="do_nothing",
                tool_args={"reason_category": "no_compliant_action_available",
                           "rationale": "Heuristic: no viable recovery strategy."}
            )

        primary = strategies[0]

        if primary == "convert_to_emi" and enriched.emi_allowed:
            months = 3 if amount < 15000 else 6
            instalment = round(amount / months, 2)
            return DecisionResult(
                event_id=event_id, tool_called="convert_to_emi",
                tool_args={
                    "emi_months": months,
                    "message_to_customer": (
                        f"Hi {customer.name.split()[0]}, your payment of Rs{amount:,.0f} "
                        f"couldn't go through. We can split it into {months} easy monthly "
                        f"instalments of Rs{instalment:,.0f} each!"
                    ),
                    "channel": "whatsapp",
                    "rationale": f"Insufficient funds on Rs{amount:,.0f}; EMI allowed; {months}-month plan."
                }
            )

        if primary == "schedule_promise_to_pay" and enriched.promise_to_pay_allowed:
            promised = (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
            notes = event.invoice.invoice_notes if event.invoice else "customer intent noted"
            return DecisionResult(
                event_id=event_id, tool_called="schedule_promise_to_pay",
                tool_args={
                    "promised_payment_date": promised,
                    "deferral_reason": notes,
                    "message_to_customer": (
                        f"Hi {customer.name.split()[0]}, noted! We've paused reminders and "
                        f"will send a payment link on {promised}."
                    ),
                    "channel": "email",
                    "rationale": "B2B invoice with stated payment intent — PTP follow-up."
                }
            )

        if primary == "apply_discount" and enriched.discount_allowed:
            disc_pct = 10.0
            discounted = round(amount * (1 - disc_pct / 100), 2)
            return DecisionResult(
                event_id=event_id, tool_called="apply_discount",
                tool_args={
                    "discount_pct": disc_pct,
                    "discount_type": "one_time",
                    "message_to_customer": (
                        f"Hi {customer.name.split()[0]}, here's a {disc_pct:.0f}% discount — "
                        f"complete your payment of Rs{discounted:,.2f} now!"
                    ),
                    "channel": "whatsapp",
                    "rationale": f"Recovery with {disc_pct}% discount (within policy)."
                }
            )

        if primary == "downgrade_subscription" and enriched.downgrade_allowed:
            return DecisionResult(
                event_id=event_id, tool_called="downgrade_subscription",
                tool_args={
                    "action": "downgrade_plan",
                    "target_plan_id": "plan_basic",
                    "message_to_customer": (
                        f"Hi {customer.name.split()[0]}, we've moved you to our Basic plan "
                        f"to keep access uninterrupted. Upgrade anytime!"
                    ),
                    "channel": "email",
                    "rationale": "Subscription failure; downgrade retains customer relationship."
                }
            )

        # Default: payment link
        return DecisionResult(
            event_id=event_id, tool_called="create_payment_link",
            tool_args={
                "amount_inr": amount,
                "message_to_customer": (
                    f"Hi {customer.name.split()[0]}, your payment of Rs{amount:,.0f} "
                    f"didn't go through. Please use the link below — valid 48 hours."
                ),
                "channel": "whatsapp",
                "rationale": f"Default recovery link for {event.failure.reason}."
            }
        )
