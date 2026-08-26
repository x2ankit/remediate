"""
orchestrator.py  —  Recovery Pipeline Orchestrator
Connects: Event -> Enrich -> Decide -> Execute -> Audit
"""
from __future__ import annotations
import logging
from .event_normalizer import RecoveryEvent
from .context_aggregator import enrich
from .decision_engine import DecisionEngine, DecisionResult
from .remediation_executor import execute, RecoveryOutcome
from .audit_logger import AuditLogger

logger = logging.getLogger("orchestrator")


class RecoveryOrchestrator:
    def __init__(self, policy: dict, engine: DecisionEngine, audit: AuditLogger):
        self.policy = policy
        self.engine = engine
        self.audit = audit

    def process(self, event: RecoveryEvent) -> RecoveryOutcome:
        enriched = enrich(event, self.policy)

        if enriched.recovery_priority == "SKIP" and not enriched.human_escalation_required:
            decision = DecisionResult(
                event_id=event.event_id, tool_called="do_nothing",
                tool_args={"reason_category": "recovery_probability_too_low",
                           "rationale": f"SKIP priority. Probability={enriched.estimated_recovery_probability_pct:.1f}%."}
            )
        else:
            decision = self.engine.decide(enriched)

        outcome = execute(decision, enriched)
        self.audit.log(event, enriched, decision, outcome)

        if event.event_index % 50 == 0:
            logger.info(
                f"[{event.event_index:4d}] {event.failure.reason:40s} -> "
                f"{decision.tool_called:30s} | Rs{outcome.amount_recovered_inr:>8,.0f}"
            )
        return outcome
