"""
dashboard_server.py
-------------------
Minimal HTTP server to serve the live batch results dashboard.
Reads audit_log.jsonl and serves a real-time updated dashboard.

Usage:
  python dashboard_server.py    # serves on http://localhost:8080
"""

import http.server
import json
import os
from pathlib import Path

BASE_DIR   = Path(__file__).parent
AUDIT_FILE = BASE_DIR / "audit_log.jsonl"
PORT       = 8080


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress request logs

    def do_GET(self):
        if self.path == "/":
            self.serve_dashboard()
        elif self.path == "/api/results":
            self.serve_results_json()
        else:
            self.send_error(404)

    def serve_results_json(self):
        records = []
        if AUDIT_FILE.exists():
            with open(AUDIT_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(records, ensure_ascii=False).encode())

    def serve_dashboard(self):
        html = open(BASE_DIR / "dashboard.html", encoding="utf-8").read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())


if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"Dashboard → http://localhost:{PORT}")
    server.serve_forever()
