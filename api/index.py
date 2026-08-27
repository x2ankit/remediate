import json
import asyncio
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
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

# Connection manager for WebSockets
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass # client disconnected

manager = ConnectionManager()

@app.get("/api/results")
def get_results():
    db = SessionLocal()
    try:
        # For simplicity, load all (in prod, paginate)
        records = db.query(AuditRecord).order_by(AuditRecord.id).all()
        return [r.to_dict() for r in records]
    finally:
        db.close()

@app.post("/api/internal/broadcast")
async def broadcast_event(request: Request):
    data = await request.json()
    await manager.broadcast(data)
    return {"status": "ok"}

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # wait for messages from client (ping/keepalive)
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/")
def read_root():
    return FileResponse(BASE_DIR / "public" / "index.html")
