"""In-memory chat state shared by every browser connected to one Streamlit server.

Data lives only in server memory: it is lost when the app restarts or sleeps.
"""

import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

DEFAULT_ROOM = "lobby"
MAX_MESSAGES_PER_ROOM = 200
MAX_ROOM_NAME = 30
MAX_AI_NAME = 20
PRESENCE_TIMEOUT_SEC = 15


@dataclass(frozen=True)
class Message:
    room: str
    author: str
    text: str
    ts: float
    is_ai: bool = False


class ChatStore:
    def __init__(self, max_messages=MAX_MESSAGES_PER_ROOM, presence_timeout=PRESENCE_TIMEOUT_SEC,
                 listener: Callable[[dict], None] | None = None):
        # listener receives a record for every new room, message and AI name (used for persistence).
        self._listener = listener
        self._lock = threading.Lock()
        self._max_messages = max_messages
        self._presence_timeout = presence_timeout
        self._rooms: dict[str, list[Message]] = {DEFAULT_ROOM: []}
        self._ai_names: dict[str, str] = {}  # room -> name the AI answers to besides @AI
        # room -> {session_id: (nickname, last_seen)}
        self._presence: dict[str, dict[str, tuple[str, float]]] = {}

    def rooms(self) -> list[str]:
        with self._lock:
            return list(self._rooms)

    def create_room(self, name: str) -> str:
        name = name.strip()[:MAX_ROOM_NAME]
        if not name:
            raise ValueError("방 이름이 비어 있습니다.")
        with self._lock:
            is_new = name not in self._rooms
            self._rooms.setdefault(name, [])
        if is_new and self._listener:
            self._listener({"type": "room", "room": name, "ts": time.time()})
        return name

    def add_message(self, room: str, author: str, text: str, is_ai: bool = False, now: float | None = None) -> Message:
        msg = Message(room, author, text, now if now is not None else time.time(), is_ai)
        self._append(msg)
        if self._listener:
            self._listener({"type": "message", **asdict(msg)})
        return msg

    def set_ai_name(self, room: str, name: str) -> str:
        name = name.strip()[:MAX_AI_NAME]
        if not name or name.startswith("@"):
            raise ValueError("사용할 수 없는 이름입니다.")
        with self._lock:
            self._ai_names[room] = name
        if self._listener:
            self._listener({"type": "ai_name", "room": room, "name": name, "ts": time.time()})
        return name

    def ai_name(self, room: str) -> str | None:
        with self._lock:
            return self._ai_names.get(room)

    def restore(self, records: list[dict]) -> None:
        """Rebuild rooms, messages and AI names from saved records, without re-notifying the listener."""
        for r in sorted(records, key=lambda r: r.get("ts", 0)):
            if r.get("type") == "room":
                with self._lock:
                    self._rooms.setdefault(r["room"], [])
            elif r.get("type") == "ai_name":
                with self._lock:
                    self._ai_names[r["room"]] = r["name"]
            elif r.get("type") == "message":
                self._append(Message(r["room"], r["author"], r["text"], r["ts"], r.get("is_ai", False)))

    def _append(self, msg: Message) -> None:
        with self._lock:
            msgs = self._rooms.setdefault(msg.room, [])
            msgs.append(msg)
            del msgs[: -self._max_messages]

    def messages(self, room: str) -> list[Message]:
        with self._lock:
            return list(self._rooms.get(room, []))

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
