"""
audit_logger.py  —  Structured JSONL audit trail
One record per event capturing: event -> enrichment -> decision -> outcome.
"""
from __future__ import annotations
import json, logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from .event_normalizer import RecoveryEvent
from .context_aggregator import EnrichedContext
from .decision_engine import DecisionResult
from .remediation_executor import RecoveryOutcome

logger = logging.getLogger("audit_logger")


def build_audit_record(event: RecoveryEvent, enriched: EnrichedContext,
                        decision: DecisionResult, outcome: RecoveryOutcome) -> dict:
    return {
        "audit_ts": datetime.now(timezone.utc).isoformat(),
        "event_id": event.event_id,
        "event_index": event.event_index,
        "event_type": event.event_type,
        "merchant_id": event.merchant_id,
        "customer": {
            "id": event.customer.id,
            "name": event.customer.name,
            "is_b2b": event.customer.is_b2b,
            "previous_interventions_30d": event.customer.previous_interventions_30d,
        },
        "payment": {
            "payment_id": event.payment.payment_id,
            "amount_inr": event.payment.amount_inr,
            "product_type": event.payment.product_type,
            "product_name": event.payment.product_name,
            "method": event.payment.method,
        },
        "failure": {
            "code": event.failure.code,
            "source": event.failure.source,
            "reason": event.failure.reason,
            "is_fraud_flag": event.failure.is_fraud_flag,
            "is_late_auth_risk": event.failure.is_late_auth_risk,
        },
        "enrichment": {
            "recovery_priority": enriched.recovery_priority,
            "estimated_recovery_probability_pct": enriched.estimated_recovery_probability_pct,
            "suggested_strategies": enriched.suggested_strategies,
            "must_do_nothing": enriched.must_do_nothing,
            "policy_violations": [{"rule": v.rule, "reason": v.reason} for v in enriched.policy_violations],
        },
        "decision": {
            "tool_called": decision.tool_called,
            "tool_args_summary": {k: v for k, v in decision.tool_args.items() if k != "message_to_customer"},
            "rationale": decision.tool_args.get("rationale", ""),
            "fallback_used": decision.fallback_used,
            "error": decision.error,
        },
        "outcome": {
            "success": outcome.success,
            "action_taken": outcome.action_taken,
            "amount_targeted_inr": outcome.amount_targeted_inr,
            "amount_recovered_inr": outcome.amount_recovered_inr,
            "retries_attempted": outcome.retries_attempted,
            "error": outcome.error,
        },
        "ground_truth_hint": event.recovery_hint,
    }


class AuditLogger:
    def __init__(self, output_path: str):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.output_path, "w", encoding="utf-8")
        self._count = 0
        logger.info(f"Audit log -> {self.output_path}")

    def log(self, event, enriched, decision, outcome):
        record = build_audit_record(event, enriched, decision, outcome)
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()
        self._count += 1

    def close(self):
        self._file.close()
        logger.info(f"Audit log closed. {self._count} records -> {self.output_path}")

    def __enter__(self): return self
    def __exit__(self, *args): self.close()
