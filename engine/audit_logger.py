"""
audit_logger.py  —  Structured JSONL audit trail and SQLite DB logger
One record per event capturing: event -> enrichment -> decision -> outcome.
"""
from __future__ import annotations
import json, logging, requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from .event_normalizer import RecoveryEvent
from .context_aggregator import EnrichedContext
from .decision_engine import DecisionResult
from .remediation_executor import RecoveryOutcome
from .db import SessionLocal, AuditRecord, init_db

logger = logging.getLogger("audit_logger")

# Initialize database tables
init_db()

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
        self.db = SessionLocal()
        logger.info(f"Audit log initialized (JSONL: {self.output_path} + SQLite)")

    def log(self, event, enriched, decision, outcome):
        record_dict = build_audit_record(event, enriched, decision, outcome)
        
        # 1. Log to JSONL (legacy)
        self._file.write(json.dumps(record_dict, ensure_ascii=False) + "\n")
        self._file.flush()
        
        # 2. Log to SQLite
        db_record = AuditRecord(
            event_id=record_dict["event_id"],
            event_index=record_dict["event_index"],
            event_type=record_dict["event_type"],
            merchant_id=record_dict["merchant_id"],
            customer_json=json.dumps(record_dict["customer"]),
            payment_json=json.dumps(record_dict["payment"]),
            failure_json=json.dumps(record_dict["failure"]),
            enrichment_json=json.dumps(record_dict["enrichment"]),
            decision_json=json.dumps(record_dict["decision"]),
            outcome_json=json.dumps(record_dict["outcome"]),
            tool_called=record_dict["decision"]["tool_called"],
            success=record_dict["outcome"]["success"],
            amount_targeted_inr=record_dict["outcome"]["amount_targeted_inr"],
            amount_recovered_inr=record_dict["outcome"]["amount_recovered_inr"]
        )
        self.db.add(db_record)
        self.db.commit()
        
            
        self._count += 1

    def close(self):
        self._file.close()
        self.db.close()
        logger.info(f"Audit log closed. {self._count} records.")

    def __enter__(self): return self
    def __exit__(self, *args): self.close()
