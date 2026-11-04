"""Chat rooms and messages in the database; who is online stays in memory.

Every saved room/message/AI name is also passed to `listener` as a record
(the Telegram bot forwards messages from it).
"""

import threading
import time
from dataclasses import asdict
from typing import Callable

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

import db
from models import Message

DEFAULT_ROOM = "lobby"
MAX_MESSAGES_PER_ROOM = 200  # how many recent messages a room shows
MAX_ROOM_NAME = 30
MAX_AI_NAME = 20
PRESENCE_TIMEOUT_SEC = 15

__all__ = ["ChatStore", "DEFAULT_ROOM", "Message"]


class ChatStore:
    def __init__(self, max_messages=MAX_MESSAGES_PER_ROOM, presence_timeout=PRESENCE_TIMEOUT_SEC,
                 listener: Callable[[dict], None] | None = None, engine: Engine | None = None):
        self._engine = engine or db.make_engine("sqlite://")
        self._listener = listener
        self._max_messages = max_messages
        self._presence_timeout = presence_timeout
        self._lock = threading.Lock()
        # room -> {session_id: (nickname, last_seen)}
        self._presence: dict[str, dict[str, tuple[str, float]]] = {}
        self._ensure_room(DEFAULT_ROOM)

    def _ensure_room(self, name: str, user_id: int | None = None) -> bool:
        """Create the room if missing; True if it was created."""
        try:
            with self._engine.begin() as conn:
                conn.execute(insert(db.rooms).values(name=name, created_by=user_id, ts=time.time()))
            return True
        except IntegrityError:
            return False

    def rooms(self) -> list[str]:
        with self._engine.connect() as conn:
            return list(conn.scalars(select(db.rooms.c.name).order_by(db.rooms.c.ts, db.rooms.c.name)))

    def create_room(self, name: str, user_id: int | None = None) -> str:
        name = name.strip()[:MAX_ROOM_NAME]
        if not name:
            raise ValueError("방 이름이 비어 있습니다.")
        if self._ensure_room(name, user_id) and self._listener:
            self._listener({"type": "room", "room": name, "ts": time.time()})
        return name

    def add_message(self, room: str, author: str, text: str, is_ai: bool = False, now: float | None = None,
                    user_id: int | None = None) -> Message:
        self._ensure_room(room)
        ts = now if now is not None else time.time()
        with self._engine.begin() as conn:
            msg_id = conn.execute(insert(db.messages).values(
                room=room, author=author, text=text, is_ai=is_ai, ts=ts, user_id=user_id)).inserted_primary_key[0]
        msg = Message(room, author, text, ts, is_ai, msg_id, user_id)
        if self._listener:
            self._listener({"type": "message", **asdict(msg)})
        return msg

    def messages(self, room: str) -> list[Message]:
        """The room's most recent visible messages, oldest first."""
        m = db.messages.c
        query = (select(db.messages).where(m.room == room, m.hidden_at.is_(None))
                 .order_by(m.id.desc()).limit(self._max_messages))
        with self._engine.connect() as conn:
            rows = conn.execute(query).all()
        return [Message(r.room, r.author, r.text, r.ts, r.is_ai, r.id, r.user_id) for r in reversed(rows)]

    def hide_message(self, message_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(db.messages).where(db.messages.c.id == message_id).values(hidden_at=time.time()))

    def set_ai_name(self, room: str, name: str) -> str:
        name = name.strip()[:MAX_AI_NAME]
        if not name or name.startswith("@"):
            raise ValueError("사용할 수 없는 이름입니다.")
        self._ensure_room(room)
        with self._engine.begin() as conn:
            conn.execute(update(db.rooms).where(db.rooms.c.name == room).values(ai_name=name))
        if self._listener:
            self._listener({"type": "ai_name", "room": room, "name": name, "ts": time.time()})
        return name

    def ai_name(self, room: str) -> str | None:
        with self._engine.connect() as conn:
            return conn.scalar(select(db.rooms.c.ai_name).where(db.rooms.c.name == room))

    # ---- presence (memory only) ----

    def touch(self, room: str, session_id: str, nickname: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            for users in self._presence.values():
                users.pop(session_id, None)  # a session is in one room at a time
            self._presence.setdefault(room, {})[session_id] = (nickname, now)

    def online(self, room: str, now: float | None = None) -> list[str]:
        now = now if now is not None else time.time()
        with self._lock:
            users = self._presence.get(room, {})
            for sid in [s for s, (_, seen) in users.items() if now - seen > self._presence_timeout]:
                del users[sid]
            return sorted({nick for nick, _ in users.values()})
