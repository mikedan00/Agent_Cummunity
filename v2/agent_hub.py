"""Personal AI agents: a user's own Claude Code / Codex / ... running on their laptop.

A user creates an agent in the web app and gets a token once. The local
connector (agent_connector.py) uses that token to fetch tasks — the agent was
@mentioned — and to send back replies. Only a hash of the token is kept.
"""

import hashlib
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

import agent_connector

ONLINE_SEC = 40  # the connector sends a heartbeat every 20s
MAX_NAME = 30

# cli id -> label shown to users
CLIS = {cli: spec["label"] for cli, spec in agent_connector.CLIS.items()}


@dataclass(frozen=True)
class PersonalAgent:
    name: str
    owner: str
    cli: str
    token_hash: str
    ts: float


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AgentHub:
    def __init__(self, listener: Callable[[dict], None] | None = None):
        self._listener = listener
        self._cond = threading.Condition()
        self._agents: dict[str, PersonalAgent] = {}
        self._last_seen: dict[str, float] = {}
        self._queues: dict[str, list[dict]] = {}
        self._in_flight: dict[str, dict] = {}  # task id -> task, taken by a connector
        self._next_id = 0

    # ---- registration ----

    def create(self, owner: str, cli: str, now: float | None = None) -> tuple[PersonalAgent, str]:
        """Create an agent named '<owner>_<cli>' (number added if taken). Returns (agent, token)."""
        if cli not in CLIS:
            raise ValueError(f"지원하지 않는 CLI: {cli}")
        base = re.sub(r"\W", "", owner)[: MAX_NAME - len(cli) - 1] or "user"
        token = secrets.token_urlsafe(24)
        with self._cond:
            name, n = f"{base}_{cli}", 2
            while name in self._agents:
                name, n = f"{base}_{cli}{n}", n + 1
            agent = PersonalAgent(name, owner, cli, _hash(token), now if now is not None else time.time())
            self._agents[name] = agent
        if self._listener:
            self._listener({"type": "personal_agent", **asdict(agent)})
        return agent, token

    def restore(self, records: list[dict]) -> None:
        for r in records:
            if r.get("type") == "personal_agent":
                with self._cond:
                    self._agents[r["name"]] = PersonalAgent(r["name"], r["owner"], r["cli"], r["token_hash"], r["ts"])

    def authenticate(self, token: str) -> PersonalAgent | None:
        """Return the agent for this token and mark it online."""
        h = _hash(token or "")
        with self._cond:
            agent = next((a for a in self._agents.values() if secrets.compare_digest(a.token_hash, h)), None)
            if agent:
                self._last_seen[agent.name] = time.time()
            return agent

    def agents(self) -> list[PersonalAgent]:
        with self._cond:
            return sorted(self._agents.values(), key=lambda a: a.ts)

    def names(self) -> set[str]:
        with self._cond:
            return set(self._agents)

    def is_online(self, name: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        with self._cond:
            return now - self._last_seen.get(name, 0) < ONLINE_SEC

    # ---- tasks ----

    def enqueue(self, name: str, task: dict) -> dict:
        with self._cond:
            self._next_id += 1
            task = {**task, "id": f"t{self._next_id}"}
            self._queues.setdefault(name, []).append(task)
            self._cond.notify_all()
        return task

    def next_task(self, name: str, timeout: float) -> dict | None:
        """Wait up to `timeout` seconds for this agent's next task."""
        with self._cond:
            self._cond.wait_for(lambda: self._queues.get(name), timeout=timeout)
            queue = self._queues.get(name)
            if not queue:
                return None
            task = queue.pop(0)
            self._in_flight[task["id"]] = {**task, "agent": name}
            return task

    def complete(self, name: str, task_id: str) -> dict | None:
        """Return the finished task, or None if it is unknown or belongs to another agent."""
        with self._cond:
            task = self._in_flight.get(task_id)
            if not task or task["agent"] != name:
                return None
            return self._in_flight.pop(task_id)
