"""Lobby (signaling) server for the peer-to-peer version.

Browsers in a room talk to each other directly over WebRTC data channels. The lobby
only introduces them: it tells a newcomer who is in the room, and forwards the WebRTC
offers/answers/ICE candidates between two peers. Chat messages don't pass through it,
except as a fallback: when two browsers can't connect directly (strict mobile or office
networks), they relay their already-signed messages through the lobby instead.
If the lobby goes away, rooms that are already connected directly keep working; only new
joins (and relayed links) fail.

Each peer gets a join ticket: the lobby signs "room|peer id|join order|peer key id" with its
ECDSA P-256 key, so no browser can claim an earlier join order (and with it the host role)
or reuse another peer's ticket. Set LOBBY_SIGNING_KEY (PEM) to keep the key across restarts.

It also relays AI calls to Hugging Face for the room's host (browsers can't call the HF API
directly because of CORS): with the host's own token, passed per request and never stored, or
with the operator's HF_TOKEN when set, limited per IP and per day.

Run: uvicorn lobby:app --port 7860
"""

import base64
import json
import os
import re
import secrets
import threading
import time
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).parent / "static"
ROOM_RE = re.compile(r"^[a-z0-9]{4,16}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
MAX_PEERS_PER_ROOM = 30  # a full mesh gets heavy beyond a few dozen browsers
MAX_ROOMS = 500
MAX_SIGNAL_BYTES = 64_000  # an SDP with candidates is a few KB; relayed data comes in chunks
RELAY_PER_10S = 300  # relayed messages per peer per 10 seconds (a full history is ~100 chunks)
DEFAULT_STUN = "stun:stun.l.google.com:19302"
DEFAULT_MODELS = ["google/gemma-4-26B-A4B-it:deepinfra", "Qwen/Qwen3.6-27B", "Qwen/Qwen2.5-7B-Instruct"]
OPERATOR_AI_PER_HOUR = int(os.environ.get("OPERATOR_AI_PER_HOUR", "60"))  # per IP
OPERATOR_AI_PER_DAY = int(os.environ.get("OPERATOR_AI_PER_DAY", "2000"))  # all rooms
MAX_PROMPT_CHARS = 20_000
CONNECTOR_FILE = Path(__file__).parent / "agent_connector.py"


def ice_servers() -> list[dict]:
    """STUN for every room; a TURN relay too when TURN_URL is set (for networks that block direct links)."""
    servers = [{"urls": [u.strip() for u in os.environ.get("STUN_URLS", DEFAULT_STUN).split(",") if u.strip()]}]
    if turn := os.environ.get("TURN_URL"):
        servers.append({"urls": [u.strip() for u in turn.split(",")],
                        "username": os.environ.get("TURN_USERNAME", ""),
                        "credential": os.environ.get("TURN_CREDENTIAL", "")})
    return servers


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def load_signing_key() -> ec.EllipticCurvePrivateKey:
    if pem := os.environ.get("LOBBY_SIGNING_KEY"):
        return serialization.load_pem_private_key(pem.encode(), password=None)
    return ec.generate_private_key(ec.SECP256R1())  # changes on restart: set LOBBY_SIGNING_KEY to keep it


SIGNING_KEY = load_signing_key()


def public_jwk() -> dict:
    numbers = SIGNING_KEY.public_key().public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": b64url(numbers.x.to_bytes(32, "big")),
            "y": b64url(numbers.y.to_bytes(32, "big")), "ext": True}


def sign_ticket(room: str, peer_id: str, seq: int, key_id: str) -> str:
    """ECDSA P-256 / SHA-256 signature in the raw r||s form that WebCrypto verifies."""
    der = SIGNING_KEY.sign(f"{room}|{peer_id}|{seq}|{key_id}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


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


# ---- AI relay ----

def models() -> list[str]:
    raw = os.environ.get("HF_MODELS", "")
    return [m.strip() for m in raw.split(",") if m.strip()] or list(DEFAULT_MODELS)


class OperatorQuota:
    """Calls on the operator's token: per IP per hour, and for everyone per day."""

    def __init__(self):
        self.lock = threading.Lock()
        self.by_ip: dict[str, list[float]] = {}
        self.day, self.today = date.today(), 0

    def take(self, ip: str, now: float | None = None) -> str | None:
        now = now or time.time()
        with self.lock:
            if self.day != date.today():
                self.day, self.today = date.today(), 0
            recent = [t for t in self.by_ip.get(ip, []) if now - t < 3600]
            if len(recent) >= OPERATOR_AI_PER_HOUR:
                return f"운영자 토큰은 시간당 {OPERATOR_AI_PER_HOUR}회까지 쓸 수 있습니다."
            if self.today >= OPERATOR_AI_PER_DAY:
                return "오늘 운영자 토큰 사용량이 한도에 도달했습니다."
            self.by_ip[ip] = recent + [now]
            self.today += 1
        return None


quota = OperatorQuota()


class AiRequest(BaseModel):
    messages: list[dict]
    model: str | None = None
    max_tokens: int = 1024
    token: str | None = None  # the host's own HF token; used for this call only, never stored


def chat_completion(token: str, model: str, messages: list[dict], max_tokens: int) -> str:
    from huggingface_hub import InferenceClient  # imported here: the lobby itself doesn't need it
    name, _, provider = model.partition(":")
    client = InferenceClient(model=name, provider=provider or None, token=token, timeout=90)
    resp = client.chat_completion(messages, max_tokens=max_tokens)
    return (resp.choices[0].message.content or "").strip()


@app.get("/api/ai/status")
def ai_status():
    return {"operator": bool(os.environ.get("HF_TOKEN")), "models": models(),
            "operator_per_hour": OPERATOR_AI_PER_HOUR}


@app.post("/api/ai")
def ai(body: AiRequest, request: Request):
    if sum(len(str(m.get("content", ""))) for m in body.messages) > MAX_PROMPT_CHARS:
        return JSONResponse({"error": "대화가 너무 깁니다."}, status_code=400)
    model = body.model if body.model in models() else models()[0]
    token = (body.token or "").strip()
    if not token:
        token = os.environ.get("HF_TOKEN", "")
        if not token:
            return JSONResponse({"error": "운영자 토큰이 설정되지 않았습니다. 호스트의 HF 토큰을 넣어 주세요."}, status_code=400)
        if problem := quota.take(request.client.host if request.client else "?"):
            return JSONResponse({"error": problem}, status_code=429)
    try:
        return {"text": chat_completion(token, model, body.messages, max(64, min(body.max_tokens, 2048))),
                "model": model}
    except Exception as e:  # don't echo request details (they may hold the token)
        return JSONResponse({"error": f"AI 호출 실패 ({type(e).__name__})"}, status_code=502)


@app.get("/download/agent_connector.py")
def download_connector():
    return FileResponse(CONNECTOR_FILE, filename="agent_connector.py", media_type="text/x-python")


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
async def lobby(ws: WebSocket, room: str, key: str = ""):
    await ws.accept()
    if not ROOM_RE.match(room) or not KEY_ID_RE.match(key):
        await ws.close(code=4000, reason="bad room code or key")
        return
    if room not in rooms.rooms and len(rooms.rooms) >= MAX_ROOMS:
        await ws.close(code=4001, reason="lobby is full")
        return
    if len(rooms.rooms.get(room, {})) >= MAX_PEERS_PER_ROOM:
        await ws.close(code=4002, reason="room is full")
        return
    peer_id, seq, existing = rooms.add(room, ws)
    relayed: list[float] = []
    await ws.send_json({"type": "welcome", "id": peer_id, "seq": seq, "peers": existing,
                        "iceServers": ice_servers(), "ticket": sign_ticket(room, peer_id, seq, key),
                        "lobbyKey": public_jwk()})
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
            kind = msg.get("type")
            if kind not in ("signal", "relay") or target is None:
                continue
            if kind == "relay":
                now = time.monotonic()
                relayed[:] = [t for t in relayed if now - t < 10] + [now]
                if len(relayed) > RELAY_PER_10S:
                    continue  # too much: the sender's own retries and timeouts deal with it
            try:
                await target.send_json({"type": kind, "from": peer_id, "data": msg.get("data")})
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
