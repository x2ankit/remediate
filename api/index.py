import json
from pathlib import Path
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR = Path(__file__).parent.parent
AUDIT_FILE = BASE_DIR / "outputs" / "audit_log.jsonl"

@app.route("/api/results")
def get_results():
    records = []
    if AUDIT_FILE.exists():
        with open(AUDIT_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    
    return jsonify(records)

# Vercel needs the app exposed (often just the app variable is enough for python functions)
