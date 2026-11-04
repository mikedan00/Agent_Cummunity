"""Usage limits: per-user daily counts in the database, per-minute message rate in memory,
per-agent hourly calls from the task table, and a service-wide daily cap on AI calls.

Days start at midnight Asia/Seoul.
"""

import threading
import time
from collections import deque
from datetime import date, datetime
from typing import Callable

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

import db
from ai_agent import TIMEZONE
from models import User

DEFAULT_PLANS = {
    "free": {"ai_per_day": 30, "posts_per_day": 5, "agent_calls_per_hour": 20, "messages_per_min": 20},
}
DEFAULT_GLOBAL_AI_PER_DAY = 2000
KIND_LIMIT = {"ai_chat": "ai_per_day", "forum_post": "posts_per_day"}


def today() -> date:
    return datetime.now(TIMEZONE).date()


class Limits:
    def __init__(self, engine: Engine, plans: dict | None = None,
                 global_ai_per_day: int = DEFAULT_GLOBAL_AI_PER_DAY,
                 on_global_cap: Callable[[int], None] | None = None, clock: Callable[[], date] = today):
        self._engine = engine
        self.plans = plans or DEFAULT_PLANS
        self.global_ai_per_day = global_ai_per_day
        self._on_global_cap = on_global_cap  # called once per day when the cap is first hit
        self._clock = clock
        self._lock = threading.Lock()
        self._recent: dict[int, deque] = {}  # user id -> timestamps of recent messages
        self._cap_notified: date | None = None

    def plan(self, user: User) -> dict:
        return self.plans.get(user.plan, self.plans["free"])

    def limit(self, user: User, kind: str) -> int:
        return self.plan(user)[KIND_LIMIT[kind]]

    # ---- daily counters ----

    def _take(self, table, key: dict, amount: int, limit: int) -> bool:
        """Add `amount` to today's counter only if it stays within `limit` (atomic)."""
        try:
            with self._engine.begin() as conn:
                conn.execute(insert(table).values(**key, count=0))
        except IntegrityError:
            pass  # today's row already exists
        cond = [table.c[k] == v for k, v in key.items()] + [table.c.count + amount <= limit]
        with self._engine.begin() as conn:
            return conn.execute(update(table).where(*cond).values(count=table.c.count + amount)).rowcount == 1

    def _give_back(self, table, key: dict, amount: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(table).where(*[table.c[k] == v for k, v in key.items()])
                         .values(count=table.c.count - amount))

    def take(self, user: User, kind: str, amount: int = 1) -> bool:
        return self._take(db.usage, {"user_id": user.id, "day": self._clock(), "kind": kind},
                          amount, self.limit(user, kind))

    def used(self, user: User, kind: str) -> int:
        u = db.usage.c
        with self._engine.connect() as conn:
            return conn.scalar(select(u.count).where(u.user_id == user.id, u.day == self._clock(),
                                                     u.kind == kind)) or 0

    def give_back(self, user: User, kind: str, amount: int = 1) -> None:
        self._give_back(db.usage, {"user_id": user.id, "day": self._clock(), "kind": kind}, amount)

    def take_global_ai(self, amount: int = 1) -> bool:
        day = self._clock()
        ok = self._take(db.daily_totals, {"day": day, "kind": "ai"}, amount, self.global_ai_per_day)
        if not ok and self._on_global_cap and self._cap_notified != day:
            self._cap_notified = day
            self._on_global_cap(self.global_ai_per_day)
        return ok

    # ---- rates ----

    def allow_message(self, user: User, now: float | None = None) -> bool:
        """At most messages_per_min messages, comments and posts in any 60 seconds."""
        now = now if now is not None else time.time()
        with self._lock:
            recent = self._recent.setdefault(user.id, deque())
            while recent and now - recent[0] >= 60:
                recent.popleft()
            if len(recent) >= self.plan(user)["messages_per_min"]:
                return False
            recent.append(now)
            return True

    def agent_calls_per_hour(self) -> int:
        return self.plans["free"]["agent_calls_per_hour"]
