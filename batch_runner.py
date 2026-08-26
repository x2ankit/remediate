"""
batch_runner.py  —  Entry point for batch processing
Processes all synthetic events through the full remediation pipeline.

Usage:
  python batch_runner.py                     # heuristic mode (no API key)
  GEMINI_API_KEY=xxx python batch_runner.py  # live Gemini mode
"""
import json, logging, time, sys, os
from pathlib import Path

# Make engine package importable from repo root
sys.path.insert(0, str(Path(__file__).parent))

from engine.event_normalizer import load_events_from_jsonl
from engine.decision_engine import DecisionEngine
from engine.orchestrator import RecoveryOrchestrator
from engine.audit_logger import AuditLogger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-22s] %(levelname)s %(message)s",
)
logger = logging.getLogger("batch_runner")

ROOT         = Path(__file__).parent
EVENTS_FILE  = ROOT / "data"    / "synthetic_events.jsonl"
POLICY_FILE  = ROOT / "config"  / "merchant_policy.json"
AUDIT_FILE   = ROOT / "outputs" / "audit_log.jsonl"
OUTCOMES_FILE = ROOT / "outputs" / "batch_outcomes.jsonl"


def main():
    logger.info("=" * 70)
    logger.info("  remediate — AI Financial Remediation Engine")
    logger.info("=" * 70)

    if not EVENTS_FILE.exists():
        logger.error(f"Events file not found: {EVENTS_FILE}")
        logger.error("Run: python scripts/generate_events.py")
        sys.exit(1)

    with open(POLICY_FILE, encoding="utf-8") as f:
        policy = json.load(f)
    logger.info(f"Policy loaded: {policy.get('merchant_name')}")

    events = load_events_from_jsonl(str(EVENTS_FILE))
    logger.info(f"Events loaded: {len(events)}")

    engine = DecisionEngine()
    mode   = "LLM (Gemini)" if engine._client else "Heuristic (no API key)"
    logger.info(f"Decision mode: {mode}")

    start = time.time()
    outcomes = []

    with AuditLogger(str(AUDIT_FILE)) as audit:
        orch = RecoveryOrchestrator(policy=policy, engine=engine, audit=audit)
        for event in events:
            outcome = orch.process(event)
            outcomes.append(outcome.to_dict())

    elapsed = time.time() - start

    with open(OUTCOMES_FILE, "w", encoding="utf-8") as f:
        for o in outcomes:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    total_targeted  = sum(o["amount_targeted_inr"] for o in outcomes)
    total_recovered = sum(o["amount_recovered_inr"] for o in outcomes)
    do_nothing_count = sum(1 for o in outcomes if o["tool_called"] == "do_nothing")

    logger.info("=" * 70)
    logger.info(f"  Completed in {elapsed:.1f}s")
    logger.info(f"  Events processed   : {len(outcomes)}")
    logger.info(f"  Actions taken      : {len(outcomes) - do_nothing_count}")
    logger.info(f"  Do-nothing         : {do_nothing_count}")
    logger.info(f"  Revenue at-risk    : Rs{total_targeted:>12,.0f}")
    logger.info(f"  Revenue recovered  : Rs{total_recovered:>12,.0f}")
    logger.info(f"  Recovery rate      : {total_recovered/total_targeted*100:.1f}%")
    logger.info(f"  Audit log          : {AUDIT_FILE}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
