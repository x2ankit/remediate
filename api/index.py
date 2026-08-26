import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent.parent
AUDIT_FILE = Path(__file__).parent / "audit_log.jsonl"
if not AUDIT_FILE.exists():
    AUDIT_FILE = BASE_DIR / "audit_log.jsonl"

@app.get("/api/results")
def get_results():
    records = []
    if AUDIT_FILE.exists():
        with open(AUDIT_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    
    return records

@app.get("/")
def read_root():
    return FileResponse(BASE_DIR / "public" / "index.html")
