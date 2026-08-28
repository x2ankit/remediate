import json
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

class AuditRecord(Base):
    __tablename__ = "audit_records"

    id = Column(Integer, primary_key=True, index=True)
    audit_ts = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    event_id = Column(String, index=True)
    event_index = Column(Integer)
    event_type = Column(String)
    merchant_id = Column(String)
    
    # We will store complex nested objects as JSON strings to keep it simple for now
    customer_json = Column(String)
    payment_json = Column(String)
    failure_json = Column(String)
    enrichment_json = Column(String)
    decision_json = Column(String)
    outcome_json = Column(String)
    
    # Searchable high-level fields
    tool_called = Column(String, index=True)
    success = Column(Boolean)
    amount_targeted_inr = Column(Float)
    amount_recovered_inr = Column(Float)

    def to_dict(self):
        return {
            "audit_ts": self.audit_ts.isoformat() if self.audit_ts else None,
            "event_id": self.event_id,
            "event_index": self.event_index,
            "event_type": self.event_type,
            "merchant_id": self.merchant_id,
            "customer": json.loads(self.customer_json) if self.customer_json else {},
            "payment": json.loads(self.payment_json) if self.payment_json else {},
            "failure": json.loads(self.failure_json) if self.failure_json else {},
            "enrichment": json.loads(self.enrichment_json) if self.enrichment_json else {},
            "decision": json.loads(self.decision_json) if self.decision_json else {},
            "outcome": json.loads(self.outcome_json) if self.outcome_json else {},
            "tool_called": self.tool_called,
            "success": self.success,
            "amount_targeted_inr": self.amount_targeted_inr,
            "amount_recovered_inr": self.amount_recovered_inr
        }

DATABASE_URL = "postgresql://neondb_owner:npg_VN0ALXCn1rWh@ep-rough-butterfly-ae0mppce-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    import os
    os.makedirs(os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"), exist_ok=True)
    Base.metadata.create_all(bind=engine)
