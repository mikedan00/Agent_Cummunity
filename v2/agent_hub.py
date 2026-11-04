"""Personal AI agents: a user's own Claude Code / Codex / ... running on their laptop.

A user creates an agent in the web app and gets a token once. The local
connector (agent_connector.py) uses that token to fetch tasks — the agent was
@mentioned — and to send back replies. Agents and tasks live in the database
(only a hash of the token is kept); who is online stays in memory.
"""

import hashlib
import re
import secrets
import threading
import time
from dataclasses import asdict
from typing import Callable

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

import agent_connector
import db
from models import PersonalAgent

ONLINE_SEC = 40  # the connector sends a heartbeat every 20s
MAX_NAME = 30
POLL_SEC = 1  # how often a waiting connector re-checks the table

# cli id -> label shown to users
CLIS = {cli: spec["label"] for cli, spec in agent_connector.CLIS.items()}

__all__ = ["CLIS", "AgentHub", "PersonalAgent"]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _agent(r) -> PersonalAgent:
    return PersonalAgent(r.name, r.owner, r.cli, r.token_hash, r.ts, r.owner_id)


def _task(r) -> dict:
    task = {"id": f"t{r.id}", "kind": r.kind, "prompt": r.prompt}
    task.update({"room": r.room} if r.kind == "chat" else {"post_id": r.post_id})
    return task


class AgentHub:
    def __init__(self, listener: Callable[[dict], None] | None = None, engine: Engine | None = None):
        self._engine = engine or db.make_engine("sqlite://")
        self._listener = listener
        self._cond = threading.Condition()  # wakes connectors waiting in this process
        self._last_seen: dict[str, float] = {}

    # ---- registration ----

    def create(self, owner: str, cli: str, now: float | None = None,
               owner_id: int | None = None) -> tuple[PersonalAgent, str]:
        """Create an agent named '<owner>_<cli>' (number added if taken). Returns (agent, token)."""
        if cli not in CLIS:
            raise ValueError(f"지원하지 않는 CLI: {cli}")
        base = re.sub(r"\W", "", owner)[: MAX_NAME - len(cli) - 1] or "user"
        token = secrets.token_urlsafe(24)
        taken = set(self._all_names())
        name, n = f"{base}_{cli}", 2
        while name in taken:
            name, n = f"{base}_{cli}{n}", n + 1
        agent = PersonalAgent(name, owner, cli, _hash(token), now if now is not None else time.time(), owner_id)
        with self._engine.begin() as conn:
            conn.execute(insert(db.personal_agents).values(**asdict(agent)))
        if self._listener:
            self._listener({"type": "personal_agent", "name": name, "owner": owner, "cli": cli, "ts": agent.ts})
        return agent, token

    def _all_names(self) -> list[str]:  # including revoked ones: names are never reused
        with self._engine.connect() as conn:
            return list(conn.scalars(select(db.personal_agents.c.name)))

    def revoke(self, name: str, owner_id: int | None = None) -> bool:
        """Revoke an agent's token. With owner_id, only that owner's agent. False if nothing changed."""
        a = db.personal_agents.c
        query = update(db.personal_agents).where(a.name == name, a.revoked_at.is_(None))
        if owner_id is not None:
            query = query.where(a.owner_id == owner_id)
        with self._engine.begin() as conn:
            return conn.execute(query.values(revoked_at=time.time())).rowcount > 0

    def authenticate(self, token: str) -> PersonalAgent | None:
        """Return the (not revoked) agent for this token and mark it online."""
        a = db.personal_agents.c
        with self._engine.connect() as conn:
            row = conn.execute(select(db.personal_agents).where(
                a.token_hash == _hash(token or ""), a.revoked_at.is_(None))).first()
        if not row:
            return None
        with self._cond:
            self._last_seen[row.name] = time.time()
        return _agent(row)

    def agents(self, owner_id: int | None = None) -> list[PersonalAgent]:
        """Active agents, oldest first (only this owner's when owner_id is given)."""
        a = db.personal_agents.c
        query = select(db.personal_agents).where(a.revoked_at.is_(None)).order_by(a.ts)
        if owner_id is not None:
            query = query.where(a.owner_id == owner_id)
        with self._engine.connect() as conn:
            return [_agent(r) for r in conn.execute(query)]

    def names(self) -> set[str]:
        return {a.name for a in self.agents()}

    def is_online(self, name: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        with self._cond:
            return now - self._last_seen.get(name, 0) < ONLINE_SEC

    # ---- tasks ----

    def enqueue(self, name: str, task: dict) -> dict:
        with self._engine.begin() as conn:
            task_id = conn.execute(insert(db.agent_tasks).values(
                agent=name, kind=task["kind"], room=task.get("room"), post_id=task.get("post_id"),
                prompt=task["prompt"], ts=time.time())).inserted_primary_key[0]
        with self._cond:
            self._cond.notify_all()
        return {**task, "id": f"t{task_id}"}

    def calls_since(self, name: str, since: float) -> int:
        """How many tasks this agent was given since `since` (for the per-agent limit)."""
        t = db.agent_tasks.c
        with self._engine.connect() as conn:
            return conn.scalar(select(func.count()).where(t.agent == name, t.ts >= since))

    def next_task(self, name: str, timeout: float) -> dict | None:
        """Claim this agent's oldest queued task, waiting up to `timeout` seconds for one."""
        deadline = time.monotonic() + timeout
        while True:
            if task := self._claim(name):
                return task
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            with self._cond:
                self._cond.wait(min(POLL_SEC, remaining))

    def _claim(self, name: str) -> dict | None:
        t = db.agent_tasks.c
        with self._engine.begin() as conn:
            for row in conn.execute(select(db.agent_tasks).where(t.agent == name, t.status == "queued")
                                    .order_by(t.id).limit(5)):
                # Conditional update: only one caller can move a task from queued to taken.
                if conn.execute(update(db.agent_tasks).where(t.id == row.id, t.status == "queued")
                                .values(status="taken")).rowcount:
                    return _task(row)
        return None

    def complete(self, name: str, task_id: str) -> dict | None:
        """Mark a taken task done and return it; None if unknown, not taken, or another agent's."""
        if not (task_id.startswith("t") and task_id[1:].isdigit()):
            return None
        t = db.agent_tasks.c
        with self._engine.begin() as conn:
            row = conn.execute(select(db.agent_tasks).where(t.id == int(task_id[1:]))).first()
            if not row or row.agent != name or row.status != "taken":
                return None
            if not conn.execute(update(db.agent_tasks).where(t.id == row.id, t.status == "taken")
                                .values(status="done")).rowcount:
                return None
        return _task(row)
