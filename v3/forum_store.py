"""Forum posts and comments in the database.

Every saved post/comment is also passed to `listener` as a record
(the Telegram bot forwards comments from it).
"""

import time
from dataclasses import asdict
from typing import Callable

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

import db
from models import Comment, Post

MAX_TITLE = 100

__all__ = ["Comment", "ForumStore", "Post"]


def _post(r) -> Post:
    return Post(r.id, r.title, r.body, r.author, r.ts, r.user_id)


class ForumStore:
    def __init__(self, listener: Callable[[dict], None] | None = None, engine: Engine | None = None):
        self._engine = engine or db.make_engine("sqlite://")
        self._listener = listener

    def create_post(self, title: str, body: str, author: str, now: float | None = None,
                    user_id: int | None = None) -> Post:
        title, body = title.strip()[:MAX_TITLE], body.strip()
        if not title or not body:
            raise ValueError("제목과 내용을 입력하세요.")
        ts = now if now is not None else time.time()
        with self._engine.begin() as conn:
            post_id = conn.execute(insert(db.posts).values(
                title=title, body=body, author=author, ts=ts, user_id=user_id)).inserted_primary_key[0]
        post = Post(post_id, title, body, author, ts, user_id)
        if self._listener:
            self._listener({"type": "post", **asdict(post)})
        return post

    def add_comment(self, post_id: int, author: str, text: str, is_ai: bool = False,
                    now: float | None = None, user_id: int | None = None) -> Comment:
        if not self.post(post_id):
            raise KeyError(f"글 #{post_id}이 없습니다.")
        ts = now if now is not None else time.time()
        with self._engine.begin() as conn:
            comment_id = conn.execute(insert(db.comments).values(
                post_id=post_id, author=author, text=text, is_ai=is_ai, ts=ts, user_id=user_id)).inserted_primary_key[0]
        comment = Comment(post_id, author, text, ts, is_ai, comment_id, user_id)
        if self._listener:
            self._listener({"type": "comment", **asdict(comment)})
        return comment

    def posts(self) -> list[Post]:
        """Visible posts, newest first."""
        query = select(db.posts).where(db.posts.c.hidden_at.is_(None)).order_by(db.posts.c.id.desc())
        with self._engine.connect() as conn:
            return [_post(r) for r in conn.execute(query)]

    def post(self, post_id: int) -> Post | None:
        query = select(db.posts).where(db.posts.c.id == post_id, db.posts.c.hidden_at.is_(None))
        with self._engine.connect() as conn:
            row = conn.execute(query).first()
        return _post(row) if row else None

    def comments(self, post_id: int) -> list[Comment]:
        c = db.comments.c
        query = select(db.comments).where(c.post_id == post_id, c.hidden_at.is_(None)).order_by(c.id)
        with self._engine.connect() as conn:
            return [Comment(r.post_id, r.author, r.text, r.ts, r.is_ai, r.id, r.user_id) for r in conn.execute(query)]

    def hide_post(self, post_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(db.posts).where(db.posts.c.id == post_id).values(hidden_at=time.time()))

    def hide_comment(self, comment_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(db.comments).where(db.comments.c.id == comment_id).values(hidden_at=time.time()))
