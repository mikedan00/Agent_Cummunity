"""The host app's web server: web UI, JSON API, realtime websocket, and the connector API.

create_app(config) builds everything; run.py serves it with uvicorn and opens the tunnel.
"""

import asyncio
import io
import secrets
import threading
from dataclasses import asdict
from pathlib import Path

import segno
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent_connector
import db
from agent_hub import AgentHub
from chat_store import DEFAULT_ROOM, ChatStore
from community import Community, load_agents
from config import Config, new_password, parse_models
from forum_store import ForumStore
from limits import Limits
from models import User
from moderation import Moderation
from sessions import ADMIN_COOKIE, COOKIE, Sessions
from users import UserStore

STATIC = Path(__file__).parent / "static"
CONNECTOR_FILE = Path(__file__).parent / "agent_connector.py"
COOKIE_MAX_AGE = 365 * 24 * 3600
MAX_WAIT_SEC = 25
MAX_REPLY_LEN = 4000


class Broadcaster:
    """Fans saved records out to every open websocket, from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._queues: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._queues.discard(queue)

    def publish(self, record: dict) -> None:
        with self._lock:
            loop, queues = self._loop, list(self._queues)
        if not loop:
            return
        for queue in queues:
            loop.call_soon_threadsafe(self._put, queue, record)

    @staticmethod
    def _put(queue: asyncio.Queue, record: dict) -> None:
        if not queue.full():  # a stuck client misses updates rather than using memory
            queue.put_nowait(record)


def apply_settings(community: Community, config: Config) -> None:
    """Push config values into the running community (after the host edits settings)."""
    community.hf_token = config["hf_token"] or None
    community.models = parse_models(config["models"])
    community.tavily_key = config["tavily_key"] or None
    community.limits.plans = {"free": dict(config["limits"])}
    community.limits.global_ai_per_day = int(config["global_ai_per_day"])


def build_community(config: Config, engine, emit, on_global_cap=None) -> Community:
    chat, forum = ChatStore(listener=emit, engine=engine), ForumStore(listener=emit, engine=engine)
    hub, users = AgentHub(listener=emit, engine=engine), UserStore(engine)
    limits = Limits(engine, on_global_cap=on_global_cap)
    community = Community(chat, forum, load_agents(), None, parse_models(config["models"]), hub=hub,
                          users=users, limits=limits, moderation=Moderation(engine, chat, forum, users))
    apply_settings(community, config)
    return community


# ---- request bodies ----

class Join(BaseModel):
    nickname: str
    password: str = ""


class Text(BaseModel):
    text: str
    model: str | None = None


class NewRoom(BaseModel):
    name: str


class NewPost(BaseModel):
    title: str
    body: str
    model: str | None = None


class Target(BaseModel):
    target: str
    target_id: int


class NewAgent(BaseModel):
    cli: str


class AgentReply(BaseModel):
    task_id: str
    text: str


def user_json(user: User | None, admin: bool) -> dict | None:
    if not user:
        return None
    return {"id": user.id, "nickname": user.nickname, "banned": user.banned, "admin": admin}


def create_app(config: Config, engine=None) -> FastAPI:
    engine = engine or db.make_engine(config["database_url"] or f"sqlite:///{config.path.parent / 'community.db'}")
    broadcaster = Broadcaster()
    listeners = [broadcaster.publish]

    def emit(record: dict) -> None:
        for listener in list(listeners):
            listener(record)

    app = FastAPI(title="Agent Community", docs_url=None, redoc_url=None)
    community = build_community(config, engine, emit, on_global_cap=lambda cap: app.state.on_global_cap(cap))
    sessions = Sessions(community.users)
    app.state.community, app.state.config, app.state.listeners = community, config, listeners
    app.state.public_url = None  # set by run.py once the tunnel is up
    app.state.lan_url = None
    app.state.telegram_bot = None
    app.state.on_global_cap = lambda cap: None
    app.state.on_settings = lambda: None

    # ---- who is asking ----

    def admin_cookie(request: Request) -> bool:
        key = request.cookies.get(ADMIN_COOKIE, "")
        return bool(key) and secrets.compare_digest(key, config["admin_key"])

    def current_user(request: Request) -> User | None:
        user = sessions.user_for(request.cookies.get(COOKIE))
        if user and admin_cookie(request) and not user.is_admin:
            community.users.set_role(user.id, "admin")  # the host's own account
            user = community.users.get(user.id)
        return user

    def writer(request: Request) -> User:
        user = current_user(request)
        if not user:
            raise HTTPException(401, "입장한 뒤에 쓸 수 있습니다.")
        if user.banned:
            raise HTTPException(403, "차단된 계정입니다. 읽기만 할 수 있습니다.")
        return user

    def require_admin(request: Request) -> None:
        user = current_user(request)
        if not (admin_cookie(request) or (user and user.is_admin)):
            raise HTTPException(403, "호스트(관리자)만 할 수 있습니다.")

    def base_url(request: Request) -> str:
        return app.state.public_url or app.state.lan_url or str(request.base_url).rstrip("/")

    def invite_url(request: Request) -> str:
        password = config["password"]
        return base_url(request) + (f"/?invite={password}" if password else "/")

    # ---- errors from the community logic ----

    @app.exception_handler(ValueError)
    async def bad_request(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(PermissionError)
    async def forbidden(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=403)

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"error": str(exc.args[0] if exc.args else exc)}, status_code=404)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    # ---- pages ----

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/admin")
    def admin_login(key: str = ""):
        if not (key and secrets.compare_digest(key, config["admin_key"])):
            raise HTTPException(403, "관리자 키가 맞지 않습니다.")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(ADMIN_COOKIE, key, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
        return response

    @app.get("/download/agent_connector.py")
    def download_connector():
        return FileResponse(CONNECTOR_FILE, filename="agent_connector.py", media_type="text/x-python")

    # ---- session ----

    @app.get("/api/state")
    def state(request: Request):
        user = current_user(request)
        admin = admin_cookie(request) or bool(user and user.is_admin)
        limits = community.limits
        usage = None
        if user:
            usage = {kind: [limits.used(user, kind), limits.limit(user, kind)] for kind in ("ai_chat", "forum_post")}
        return {
            "me": user_json(user, admin),
            "admin": admin,
            "needs_password": bool(config["password"]) and not admin,
            "rooms": community.chat.rooms(),
            "default_room": DEFAULT_ROOM,
            "agents": list(community.agents),
            "personal_agents": [{"name": a.name, "owner": a.owner, "cli": a.cli,
                                 "online": community.hub.is_online(a.name)} for a in community.hub.agents()],
            "models": community.models,
            "ai_ready": bool(community.hf_token),
            "usage": usage,
            "server_url": base_url(request),
            "invite_url": invite_url(request) if admin else None,
            "telegram_bot": app.state.telegram_bot,
        }

    @app.post("/api/join")
    def join(body: Join, request: Request):
        if current_user(request):
            raise ValueError("이미 입장했습니다.")
        taken = set(community.agents) | community.hub.names()
        required = "" if admin_cookie(request) else config["password"]  # the host needs no password
        user, token = sessions.join(body.nickname, body.password, required, taken)
        response = JSONResponse({"me": user_json(user, admin_cookie(request))})
        response.set_cookie(COOKIE, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
        if admin_cookie(request):
            community.users.set_role(user.id, "admin")
        return response

    @app.post("/api/logout")
    def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE)
        return response

    # ---- chat ----

    @app.get("/api/rooms/{room}/messages")
    def messages(room: str):
        return [asdict(m) for m in community.chat.messages(room)]

    @app.post("/api/rooms")
    def create_room(body: NewRoom, request: Request):
        return {"room": community.chat.create_room(body.name, user_id=writer(request).id)}

    @app.post("/api/rooms/{room}/messages")
    def send_message(room: str, body: Text, request: Request):
        user = writer(request)
        if not body.text.strip():
            raise ValueError("메시지를 입력하세요.")
        community.send_chat(room, user.nickname, body.text.strip()[:1000], body.model, user=user)
        return {"ok": True}

    # ---- forum ----

    @app.get("/api/posts")
    def posts():
        return [{**asdict(p), "comments": len(community.forum.comments(p.id))} for p in community.forum.posts()]

    @app.get("/api/posts/{post_id}")
    def post(post_id: int):
        found = community.forum.post(post_id)
        if not found:
            raise KeyError(f"글 #{post_id}이 없습니다.")
        return {"post": asdict(found), "comments": [asdict(c) for c in community.forum.comments(post_id)]}

    @app.post("/api/posts")
    def create_post(body: NewPost, request: Request):
        user = writer(request)
        return asdict(community.create_post(body.title, body.body, user.nickname, body.model, user=user))

    @app.post("/api/posts/{post_id}/comments")
    def comment(post_id: int, body: Text, request: Request):
        user = writer(request)
        if not body.text.strip():
            raise ValueError("댓글을 입력하세요.")
        community.add_comment(post_id, user.nickname, body.text.strip(), body.model, user=user)
        return {"ok": True}

    @app.post("/api/report")
    def report(body: Target, request: Request):
        return {"reported": community.moderation.report(writer(request).id, body.target, body.target_id)}

    # ---- my AI agents ----

    @app.get("/api/clis")
    def clis():
        return agent_connector.CLIS

    @app.get("/api/my/agents")
    def my_agents(request: Request):
        user = writer(request)
        return [{"name": a.name, "cli": a.cli, "online": community.hub.is_online(a.name)}
                for a in community.hub.agents(owner_id=user.id)]

    @app.post("/api/my/agents")
    def create_agent(body: NewAgent, request: Request):
        user = writer(request)
        agent, token = community.hub.create(user.nickname, body.cli, owner_id=user.id)
        command = f"python agent_connector.py --server {base_url(request)} --token {token} --cli {body.cli}"
        return {"name": agent.name, "token": token, "command": command}

    @app.delete("/api/my/agents/{name}")
    def revoke_agent(name: str, request: Request):
        if not community.hub.revoke(name, owner_id=writer(request).id):
            raise KeyError("내 에이전트가 아닙니다.")
        return {"ok": True}

    @app.post("/api/my/telegram-code")
    def telegram_code(request: Request):
        if not app.state.telegram_bot:
            raise ValueError("Telegram 봇이 설정되지 않았습니다.")
        return {"code": community.users.issue_link_code(writer(request).id), "bot": app.state.telegram_bot}

    # ---- admin ----

    @app.get("/api/admin/reports")
    def reports(request: Request):
        require_admin(request)
        return community.moderation.open_reports()

    @app.post("/api/admin/hide")
    def hide(body: Target, request: Request):
        require_admin(request)
        community.moderation.hide(body.target, body.target_id)
        broadcaster.publish({"type": "hidden", "target": body.target, "target_id": body.target_id})
        return {"ok": True}

    @app.post("/api/admin/dismiss")
    def dismiss(body: Target, request: Request):
        require_admin(request)
        community.moderation.resolve(body.target, body.target_id)
        return {"ok": True}

    @app.post("/api/admin/ban")
    def ban(body: Target, request: Request):
        require_admin(request)
        if not community.moderation.ban_author(body.target, body.target_id):
            raise ValueError("AI가 쓴 내용은 작성자를 차단할 수 없습니다.")
        broadcaster.publish({"type": "hidden", "target": body.target, "target_id": body.target_id})
        return {"ok": True}

    @app.get("/api/admin/settings")
    def get_settings(request: Request):
        require_admin(request)
        return config.public_view()

    @app.put("/api/admin/settings")
    async def put_settings(request: Request):
        require_admin(request)
        changes = await request.json()
        if changes.get("password") == "new":
            changes["password"] = new_password()
        config.update(changes)
        apply_settings(community, config)
        app.state.on_settings()
        return config.public_view()

    @app.get("/api/admin/qr.svg")
    def qr(request: Request):
        require_admin(request)
        buffer = io.BytesIO()
        segno.make(invite_url(request), error="m").save(buffer, kind="svg", scale=5, border=2)
        return Response(buffer.getvalue(), media_type="image/svg+xml")

    # ---- connector API (same protocol as v1/v2) ----

    def agent_from(request: Request):
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        agent = community.hub.authenticate(token if scheme.lower() == "bearer" else "")
        if not agent:
            raise HTTPException(401, "invalid token")
        return agent

    @app.post("/api/agent/hello")
    def agent_hello(request: Request):
        agent = agent_from(request)
        return {"name": agent.name, "owner": agent.owner, "cli": agent.cli}

    @app.get("/api/agent/task")
    async def agent_task(request: Request, wait: float = MAX_WAIT_SEC):
        agent = await asyncio.to_thread(agent_from, request)
        task = await asyncio.to_thread(community.hub.next_task, agent.name, max(0.0, min(wait, MAX_WAIT_SEC)))
        return {"task": task}

    @app.post("/api/agent/reply")
    def agent_reply(body: AgentReply, request: Request):
        agent = agent_from(request)
        if not community.finish_personal(agent, body.task_id, body.text[:MAX_REPLY_LEN]):
            raise KeyError("unknown task")
        return {"ok": True}

    # ---- realtime ----

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        queue = broadcaster.subscribe()
        try:
            while True:
                await websocket.send_json(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            broadcaster.unsubscribe(queue)

    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
