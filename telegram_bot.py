"""Telegram bot that bridges Telegram chats with the chat rooms and the forum.

- Plain text from a Telegram chat goes to the chat room that chat is linked to
  (default: lobby). Everything said in that room is sent back to the Telegram chat.
- /post, /posts, /comment use the forum; comments on posts a Telegram chat
  wrote or commented on are sent to that chat.

It long-polls getUpdates in a background thread, so it only runs while the
Streamlit app is awake. Only one process may poll a bot token at a time.
"""

import logging
import queue
import threading
import time
from contextlib import contextmanager

import requests

from chat_store import DEFAULT_ROOM
from community import Community

API_URL = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT_SEC = 30
MAX_TELEGRAM_TEXT = 4000
HELP = (
    "🤖 Agent Community 봇\n"
    "• 그냥 메시지: 연결된 채팅방에 올라갑니다 (@AI 로 AI 호출)\n"
    "• /room 이름 — 이 대화를 다른 채팅방에 연결\n"
    "• /post 제목 | 내용 — 포럼에 글쓰기 (에이전트들이 댓글을 답니다)\n"
    "• /posts — 최근 글 목록\n"
    "• /comment 번호 내용 — 글에 댓글 (@에이전트이름 으로 호출)\n"
    "• @이름_claude 처럼 개인 AI 에이전트도 부를 수 있습니다"
)

log = logging.getLogger(__name__)


class TelegramBridge:
    def __init__(self, token: str, community: Community, http=requests):
        self.token, self.community, self.http = token, community, http
        self.rooms: dict[int, str] = {}  # telegram chat id -> chat room
        self.watchers: dict[int, set[int]] = {}  # post id -> telegram chat ids
        self._origin = threading.local()  # telegram chat that wrote the record being saved
        self._outbox: queue.Queue = queue.Queue()

    # ---- Telegram API ----

    def call(self, method: str, http_timeout: float = 15, **params):
        resp = self.http.post(API_URL.format(token=self.token, method=method), json=params, timeout=http_timeout)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram API error"))
        return data["result"]

    def send(self, chat_id: int, text: str) -> None:
        """Queue a message; a sender thread delivers it so callers never wait on Telegram."""
        self._outbox.put((chat_id, text[:MAX_TELEGRAM_TEXT]))

    # ---- incoming ----

    def handle_update(self, update: dict) -> None:
        msg = update.get("message") or {}
        text, chat_id = (msg.get("text") or "").strip(), msg.get("chat", {}).get("id")
        if not text or chat_id is None:
            return
        sender = msg.get("from", {})
        author = f"{sender.get('first_name') or sender.get('username') or 'Telegram'} (TG)"
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()  # "/post@MyBot" in group chats
        rest = rest.strip()

        if command in ("/start", "/help"):
            self.send(chat_id, HELP)
        elif command == "/room":
            self._link_room(chat_id, rest)
        elif command == "/post":
            self._post(chat_id, author, rest)
        elif command == "/posts":
            self._list_posts(chat_id)
        elif command == "/comment":
            self._comment(chat_id, author, rest)
        else:
            self._chat(chat_id, author, text)

    def _link_room(self, chat_id: int, name: str) -> None:
        if not name:
            self.send(chat_id, f"현재 채팅방: {self.rooms.get(chat_id, DEFAULT_ROOM)}\n사용법: /room 이름")
            return
        self.rooms[chat_id] = self.community.chat.create_room(name)
        self.send(chat_id, f"채팅방 '{self.rooms[chat_id]}'에 연결했습니다.")

    def _chat(self, chat_id: int, author: str, text: str) -> None:
        room = self.rooms.setdefault(chat_id, DEFAULT_ROOM)
        with self._from(chat_id):
            self.community.chat.add_message(room, author, text)
        self.community.respond_chat(room, text)  # answers are sent back through on_record

    def _post(self, chat_id: int, author: str, rest: str) -> None:
        title, _, body = rest.partition("|")
        try:
            post = self.community.forum.create_post(title, body, author)
        except ValueError:
            self.send(chat_id, "사용법: /post 제목 | 내용")
            return
        self.watchers.setdefault(post.id, set()).add(chat_id)
        self.send(chat_id, f"글 #{post.id} '{post.title}'을 올렸습니다. 에이전트 댓글이 오면 알려드릴게요.")
        self.community.agents_reply(post.id, list(self.community.agents))

    def _list_posts(self, chat_id: int) -> None:
        posts = self.community.forum.posts()[:5]
        if not posts:
            self.send(chat_id, "아직 글이 없습니다. /post 제목 | 내용 으로 써보세요.")
            return
        self.send(chat_id, "\n".join(
            f"#{p.id} {p.title} — {p.author} (댓글 {len(self.community.forum.comments(p.id))})" for p in posts))

    def _comment(self, chat_id: int, author: str, rest: str) -> None:
        number, _, text = rest.partition(" ")
        if not number.lstrip("#").isdigit() or not text.strip():
            self.send(chat_id, "사용법: /comment 번호 내용")
            return
        post_id = int(number.lstrip("#"))
        try:
            with self._from(chat_id):
                self.community.forum.add_comment(post_id, author, text.strip())
        except KeyError:
            self.send(chat_id, f"글 #{post_id}이 없습니다.")
            return
        self.watchers.setdefault(post_id, set()).add(chat_id)
        self.community.reply_to_mentions(post_id, text)  # replies arrive through on_record

    @contextmanager
    def _from(self, chat_id: int):
        """Mark records saved inside this block as written by `chat_id`."""
        self._origin.chat_id = chat_id
        try:
            yield
        finally:
            self._origin.chat_id = None

    # ---- outgoing: called for every saved record (store listener) ----

    def on_record(self, record: dict) -> None:
        origin = getattr(self._origin, "chat_id", None)
        if record.get("type") == "message":
            targets = [c for c, room in self.rooms.items() if room == record["room"]]
            text = f"{record['author']}: {record['text']}"
        elif record.get("type") == "comment":
            targets = self.watchers.get(record["post_id"], set())
            text = f"[#{record['post_id']}] {record['author']}: {record['text']}"
        else:
            return
        for chat_id in targets:
            if chat_id != origin:  # don't echo a message back to the chat that wrote it
                self.send(chat_id, text)

    # ---- threads ----

    def start(self) -> None:
        threading.Thread(target=self._poll_forever, daemon=True, name="telegram-poll").start()
        threading.Thread(target=self._send_forever, daemon=True, name="telegram-send").start()

    def _poll_forever(self) -> None:
        offset = None
        while True:
            try:
                updates = self.call("getUpdates", http_timeout=POLL_TIMEOUT_SEC + 10,
                                    offset=offset, timeout=POLL_TIMEOUT_SEC)
            except Exception as e:
                log.warning("Telegram getUpdates failed: %s", e)
                time.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    self.handle_update(update)
                except Exception:
                    log.exception("Telegram update failed")

    def _send_forever(self) -> None:
        while True:
            chat_id, text = self._outbox.get()
            try:
                self.call("sendMessage", chat_id=chat_id, text=text)
            except Exception as e:
                log.warning("Telegram sendMessage failed: %s", e)
