"""In-memory forum (posts and comments) shared by every browser and the Telegram bot.

Like ChatStore, every change is passed to `listener` as a record so it can be
saved, and `restore` rebuilds the forum from saved records.
"""

import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

MAX_TITLE = 100


@dataclass(frozen=True)
class Post:
    id: int
    title: str
    body: str
    author: str
    ts: float


@dataclass(frozen=True)
class Comment:
    post_id: int
    author: str
    text: str
    ts: float
    is_ai: bool = False


class ForumStore:
    def __init__(self, listener: Callable[[dict], None] | None = None):
        self._listener = listener
        self._lock = threading.Lock()
        self._posts: dict[int, Post] = {}
        self._comments: dict[int, list[Comment]] = {}

    def create_post(self, title: str, body: str, author: str, now: float | None = None) -> Post:
        title, body = title.strip()[:MAX_TITLE], body.strip()
        if not title or not body:
            raise ValueError("제목과 내용을 입력하세요.")
        with self._lock:
            post = Post(max(self._posts, default=0) + 1, title, body, author,
                        now if now is not None else time.time())
            self._posts[post.id] = post
            self._comments[post.id] = []
        if self._listener:
            self._listener({"type": "post", **asdict(post)})
        return post

    def add_comment(self, post_id: int, author: str, text: str, is_ai: bool = False,
                    now: float | None = None) -> Comment:
        comment = Comment(post_id, author, text, now if now is not None else time.time(), is_ai)
        with self._lock:
            if post_id not in self._posts:
                raise KeyError(f"글 #{post_id}이 없습니다.")
            self._comments[post_id].append(comment)
        if self._listener:
            self._listener({"type": "comment", **asdict(comment)})
        return comment

    def posts(self) -> list[Post]:
        """Newest first."""
        with self._lock:
            return sorted(self._posts.values(), key=lambda p: p.id, reverse=True)

    def post(self, post_id: int) -> Post | None:
        with self._lock:
            return self._posts.get(post_id)

    def comments(self, post_id: int) -> list[Comment]:
        with self._lock:
            return list(self._comments.get(post_id, []))

    def restore(self, records: list[dict]) -> None:
        """Rebuild posts and comments from saved records, without re-notifying the listener."""
        for r in sorted(records, key=lambda r: r.get("ts", 0)):
            with self._lock:
                if r.get("type") == "post":
                    self._posts[r["id"]] = Post(r["id"], r["title"], r["body"], r["author"], r["ts"])
                    self._comments.setdefault(r["id"], [])
                elif r.get("type") == "comment" and r["post_id"] in self._posts:
                    self._comments[r["post_id"]].append(
                        Comment(r["post_id"], r["author"], r["text"], r["ts"], r.get("is_ai", False)))
