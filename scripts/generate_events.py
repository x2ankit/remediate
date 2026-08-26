"""
generate_events.py
------------------
Synthetic failure event generator — produces 500 diverse revenue-at-risk events.
Run from repo root: python scripts/generate_events.py
"""
import json, random, uuid, sys, os
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
random.seed(42)

CUSTOMER_NAMES = [
    "Arjun Mehta", "Priya Sharma", "Rohan Kapoor", "Sneha Patel",
    "Vikram Singh", "Neha Gupta", "Amit Joshi", "Divya Reddy",
    "Kiran Rao", "Siddharth Nair", "Anjali Kumar", "Rahul Verma",
    "Sunita Iyer", "Deepak Mishra", "Pooja Shah",
    "GlobalTech Pvt Ltd", "Horizon Exports", "Nexus Solutions India",
    "BlueSky Consulting", "Apex Distributors",
]

PRODUCTS = {
    "saas_monthly":       {"name": "Acme SaaS Pro (Monthly)", "amount_inr": 2999,   "type": "subscription"},
    "saas_annual":        {"name": "Acme SaaS Pro (Annual)",  "amount_inr": 29999,  "type": "subscription"},
    "saas_basic":         {"name": "Acme SaaS Basic (Monthly)","amount_inr": 999,   "type": "subscription"},
    "b2b_invoice_small":  {"name": "Consulting Invoice",       "amount_inr": 45000, "type": "invoice"},
    "b2b_invoice_large":  {"name": "Enterprise Services Invoice","amount_inr": 180000,"type": "invoice"},
    "b2c_cart_small":     {"name": "Electronics Bundle",       "amount_inr": 5499,  "type": "one_time"},
    "b2c_cart_large":     {"name": "Laptop + Accessories",     "amount_inr": 89999, "type": "one_time"},
    "b2c_course":         {"name": "Online Course Pack",       "amount_inr": 3999,  "type": "one_time"},
}

FAILURE_SCENARIOS = [
    (18, "BAD_REQUEST_ERROR", "customer",  "payment_authentication", "invalid_otp",                  "Customer entered wrong OTP",              "retry_prompt"),
    (12, "GATEWAY_ERROR",     "gateway",   "payment_authorization",  "gateway_technical_error",       "Transient gateway issue",                 "retry_later"),
    (15, "BAD_REQUEST_ERROR", "issuer",    "payment_authorization",  "insufficient_funds",            "Customer has insufficient balance",       "emi_or_discount"),
    (8,  "BAD_REQUEST_ERROR", "customer",  "payment_authentication", "card_expired",                  "Card is expired",                         "payment_link_update"),
    (5,  "SERVER_ERROR",      "razorpay",  "payment_capture",        "timeout",                       "Internal timeout — late auth possible",   "do_nothing_check_late_auth"),
    (8,  "BAD_REQUEST_ERROR", "issuer",    "payment_authorization",  "do_not_honor",                  "Bank declined — soft decline",            "retry_later_or_discount"),
    (6,  "BAD_REQUEST_ERROR", "customer",  "payment_authentication", "card_stolen",                   "Card reported stolen — STOP",             "stop_fraud"),
    (4,  "BAD_REQUEST_ERROR", "customer",  "payment_authentication", "customer_fraud_risk",           "Fraud risk flag — STOP",                  "stop_fraud"),
    (10, "BAD_REQUEST_ERROR", "issuer",    "payment_authorization",  "subscription_renewal_failed",   "Subscription auto-charge failed",         "emi_or_pause_or_downgrade"),
    (7,  "CHECKOUT_ABANDON",  "customer",  "checkout",               "cart_abandoned",                "Customer abandoned high-value cart",      "payment_link_or_discount"),
    (5,  "INVOICE_OVERDUE",   "business",  "invoice",                "invoice_overdue_b2b",           "B2B invoice overdue > 7 days",            "promise_to_pay_or_escalate"),
    (2,  "INVOICE_OVERDUE",   "business",  "invoice",                "invoice_overdue_b2b_long",      "B2B invoice overdue > 14 days",           "human_escalate"),
]
WEIGHTS = [s[0] for s in FAILURE_SCENARIOS]


def _pick_product(scenario):
    hint = scenario[6]
    if "invoice" in scenario[4]:     keys = [k for k in PRODUCTS if "invoice" in k]
    elif "subscription" in scenario[4] or "downgrade" in hint: keys = [k for k in PRODUCTS if PRODUCTS[k]["type"] == "subscription"]
    elif "cart" in scenario[4]:      keys = [k for k in PRODUCTS if "b2c" in k]
    else:                            keys = list(PRODUCTS.keys())
    return PRODUCTS[random.choice(keys)]


def _pick_customer(scenario):
    is_b2b = "invoice" in scenario[4]
    pool = CUSTOMER_NAMES[15:] if is_b2b else CUSTOMER_NAMES[:15]
    name = random.choice(pool)
    return {
        "id": f"cust_{uuid.uuid4().hex[:10]}", "name": name,
        "email": name.lower().replace(" ", ".") + "@example.com",
        "phone": f"+91{random.randint(7000000000, 9999999999)}",
        "is_b2b": is_b2b,
        "previous_interventions_30d": random.choices([0,1,2,3], weights=[50,30,15,5])[0],
        "previous_successful_payments": random.randint(0, 24),
        "last_discount_days_ago": random.choices([None,120,45,30], weights=[60,20,10,10])[0],
    }


def generate_event(index: int) -> dict:
    scenario = random.choices(FAILURE_SCENARIOS, weights=WEIGHTS, k=1)[0]
    product  = _pick_product(scenario)
    customer = _pick_customer(scenario)
    ts       = datetime.now(timezone.utc) - timedelta(minutes=random.randint(1, 43200))
    (_, code, source, step, reason, description, hint) = scenario

    event = {
        "event_id": f"evt_{uuid.uuid4().hex}", "event_index": index,
        "event_type": ("payment.failed" if code not in ("CHECKOUT_ABANDON","INVOICE_OVERDUE")
                       else "checkout.abandoned" if code == "CHECKOUT_ABANDON" else "invoice.expired"),
        "timestamp": ts.isoformat(), "merchant_id": "MERCHANT_DEMO_001",
        "customer": customer,
        "payment": {
            "payment_id": f"pay_{uuid.uuid4().hex[:16]}", "order_id": f"order_{uuid.uuid4().hex[:12]}",
            "amount_inr": product["amount_inr"], "currency": "INR",
            "method": random.choice(["card","upi","netbanking"]),
            "product_name": product["name"], "product_type": product["type"],
        },
        "error": {"code": code, "source": source, "step": step, "reason": reason, "description": description},
        "recovery_hint": hint,
    }

    if product["type"] == "subscription":
        event["subscription"] = {
            "subscription_id": f"sub_{uuid.uuid4().hex[:12]}",
            "plan_id": f"plan_{product['name'][:6].replace(' ','')}",
            "charge_at": ts.isoformat(), "total_count": 12,
            "paid_count": random.randint(1, 11), "status": "active",
        }

    if product["type"] == "invoice":
        overdue_days = random.randint(8, 60) if "long" in reason else random.randint(1, 13)
        due_date = ts - timedelta(days=overdue_days)
        event["invoice"] = {
            "invoice_id": f"inv_{uuid.uuid4().hex[:12]}",
            "due_date": due_date.date().isoformat(), "overdue_days": overdue_days,
            "invoice_notes": random.choice([
                "Awaiting client payment transfer", "Payment promised verbally",
                "Client mentioned cash-flow issues", "No response to reminders",
            ]),
        }
    return event


def main():
    out_path = Path(__file__).parent.parent / "data" / "synthetic_events.jsonl"
    out_path.parent.mkdir(exist_ok=True)
    events = [generate_event(i) for i in range(500)]
    with open(out_path, "w", encoding="utf-8") as f:
        for evt in events:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")

    counts = {}
    for evt in events:
        r = evt["error"]["reason"]
        counts[r] = counts.get(r, 0) + 1
    print(f"Generated {len(events)} events -> {out_path}")
    print("\nCategory distribution:")
    for reason, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {reason:45s} {count:4d}  ({count/5:.1f}%)")


if __name__ == "__main__":
    main()
