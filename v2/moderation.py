"""Reports from users, and what admins do about them: hide the content, dismiss, or ban the author."""

import time

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

import db
from chat_store import ChatStore
from forum_store import ForumStore
from users import UserStore

TARGETS = {"message": db.messages, "post": db.posts, "comment": db.comments}


class Moderation:
    def __init__(self, engine: Engine, chat: ChatStore, forum: ForumStore, users: UserStore):
        self._engine, self.chat, self.forum, self.users = engine, chat, forum, users

    def report(self, reporter_id: int, target: str, target_id: int) -> bool:
        """File a report; False if this user already reported it (and it's still open)."""
        if target not in TARGETS:
            raise ValueError(target)
        r = db.reports.c
        with self._engine.begin() as conn:
            if conn.scalar(select(func.count()).where(r.reporter_id == reporter_id, r.target == target,
                                                      r.target_id == target_id, r.resolved_at.is_(None))):
                return False
            conn.execute(insert(db.reports).values(reporter_id=reporter_id, target=target, target_id=target_id,
                                                   ts=time.time()))
        return True

    def open_reports(self) -> list[dict]:
        """One entry per reported item: what it says, who wrote it, how many reports."""
        r = db.reports.c
        query = (select(r.target, r.target_id, func.count().label("count"), func.min(r.ts).label("ts"))
                 .where(r.resolved_at.is_(None)).group_by(r.target, r.target_id).order_by(func.min(r.ts)))
        out = []
        with self._engine.connect() as conn:
            for row in conn.execute(query):
                table = TARGETS[row.target]
                item = conn.execute(select(table).where(table.c.id == row.target_id)).first()
                if item is None:
                    continue
                text = f"{item.title}: {item.body}" if row.target == "post" else item.text
                out.append({"target": row.target, "target_id": row.target_id, "count": row.count,
                            "author": item.author, "author_id": item.user_id, "text": text,
                            "hidden": item.hidden_at is not None})
        return out

    def hide(self, target: str, target_id: int) -> None:
        {"message": self.chat.hide_message, "post": self.forum.hide_post,
         "comment": self.forum.hide_comment}[target](target_id)
        self.resolve(target, target_id)

    def resolve(self, target: str, target_id: int) -> None:
        r = db.reports.c
        with self._engine.begin() as conn:
            conn.execute(update(db.reports).where(r.target == target, r.target_id == target_id,
                                                  r.resolved_at.is_(None)).values(resolved_at=time.time()))

    def ban_author(self, target: str, target_id: int) -> bool:
        """Ban whoever wrote the item and hide it. False for AI content (no account)."""
        table = TARGETS[target]
        with self._engine.connect() as conn:
            author_id = conn.scalar(select(table.c.user_id).where(table.c.id == target_id))
        if author_id is None:
            return False
        self.users.set_banned(author_id, True)
        self.hide(target, target_id)
        return True
