import json
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from engine.db import SessionLocal, AuditRecord, init_db

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent.parent
init_db()


@app.get("/api/results")
def get_results():
    db = SessionLocal()
    try:
        # For simplicity, load all (in prod, paginate)
        records = db.query(AuditRecord).order_by(AuditRecord.id).all()
        return [r.to_dict() for r in records]
    finally:
        db.close()


@app.get("/")
def read_root():
    return FileResponse(BASE_DIR / "public" / "index.html")
