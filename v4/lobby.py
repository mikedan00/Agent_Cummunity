"""Lobby (signaling) server for the peer-to-peer version.

Browsers in a room talk to each other directly over WebRTC data channels. The lobby
only introduces them: it tells a newcomer who is in the room, and forwards the WebRTC
offers/answers/ICE candidates between two peers. Chat messages never pass through it.
If the lobby goes away, rooms that are already connected keep working; only new joins fail.

Run: uvicorn lobby:app --port 7860
"""

import json
import os
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).parent / "static"
ROOM_RE = re.compile(r"^[a-z0-9]{4,16}$")
MAX_PEERS_PER_ROOM = 30  # a full mesh gets heavy beyond a few dozen browsers
MAX_ROOMS = 500
MAX_SIGNAL_BYTES = 64_000  # an SDP with candidates is a few KB
DEFAULT_STUN = "stun:stun.l.google.com:19302"


def ice_servers() -> list[dict]:
    """STUN for every room; a TURN relay too when TURN_URL is set (for networks that block direct links)."""
    servers = [{"urls": [u.strip() for u in os.environ.get("STUN_URLS", DEFAULT_STUN).split(",") if u.strip()]}]
    if turn := os.environ.get("TURN_URL"):
        servers.append({"urls": [u.strip() for u in turn.split(",")],
                        "username": os.environ.get("TURN_USERNAME", ""),
                        "credential": os.environ.get("TURN_CREDENTIAL", "")})
    return servers


class Rooms:
    def __init__(self):
        self.rooms: dict[str, dict[str, WebSocket]] = {}
        self.seq: dict[str, int] = {}  # per-room join counter: lower = joined earlier

    def add(self, room: str, ws: WebSocket) -> tuple[str, int, list[str]]:
        peers = self.rooms.setdefault(room, {})
        peer_id = secrets.token_hex(6)
        self.seq[room] = self.seq.get(room, 0) + 1
        existing = list(peers)
        peers[peer_id] = ws
        return peer_id, self.seq[room], existing

    def remove(self, room: str, peer_id: str) -> list[WebSocket]:
        peers = self.rooms.get(room, {})
        peers.pop(peer_id, None)
        if not peers:
            self.rooms.pop(room, None)
            self.seq.pop(room, None)
        return list(peers.values())


app = FastAPI(title="Agent Community Lobby", docs_url=None, redoc_url=None)
rooms = Rooms()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/new-room")
def new_room():
    return {"room": secrets.token_hex(4)}


@app.get("/api/health")
def health():
    return {"rooms": len(rooms.rooms), "peers": sum(len(p) for p in rooms.rooms.values())}


@app.websocket("/ws/{room}")
async def lobby(ws: WebSocket, room: str):
    await ws.accept()
    if not ROOM_RE.match(room):
        await ws.close(code=4000, reason="bad room code")
        return
    if room not in rooms.rooms and len(rooms.rooms) >= MAX_ROOMS:
        await ws.close(code=4001, reason="lobby is full")
        return
    if len(rooms.rooms.get(room, {})) >= MAX_PEERS_PER_ROOM:
        await ws.close(code=4002, reason="room is full")
        return
    peer_id, seq, existing = rooms.add(room, ws)
    await ws.send_json({"type": "welcome", "id": peer_id, "seq": seq, "peers": existing,
                        "iceServers": ice_servers()})
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_SIGNAL_BYTES:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            target = rooms.rooms.get(room, {}).get(msg.get("to"))
            if msg.get("type") == "signal" and target is not None:
                try:
                    await target.send_json({"type": "signal", "from": peer_id, "data": msg.get("data")})
                except RuntimeError:
                    pass  # the target just left
    except WebSocketDisconnect:
        pass
    finally:
        for other in rooms.remove(room, peer_id):
            try:
                await other.send_json({"type": "peer-left", "id": peer_id})
            except RuntimeError:
                pass


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
