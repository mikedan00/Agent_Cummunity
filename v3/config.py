"""Where the app keeps its data, and the host's settings (config.json).

Windows: %APPDATA%\\AgentCommunity · Linux: ~/.local/share/agent-community ·
override with AGENT_COMMUNITY_HOME.
"""

import json
import os
import secrets
import threading
from pathlib import Path

import ai_agent
from limits import DEFAULT_GLOBAL_AI_PER_DAY, DEFAULT_PLANS

PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alike characters


def data_dir() -> Path:
    if home := os.environ.get("AGENT_COMMUNITY_HOME"):
        path = Path(home)
    elif os.name == "nt":
        path = Path(os.environ.get("APPDATA", Path.home())) / "AgentCommunity"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "agent-community"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(8))


def parse_models(value) -> list[str]:
    """A list, or a string: a JSON list or comma/newline-separated model ids."""
    if not value:
        return list(ai_agent.DEFAULT_MODELS)
    if isinstance(value, str):
        value = json.loads(value) if value.strip().startswith("[") else value.replace("\n", ",").split(",")
    return [m.strip() for m in value if m.strip()]


def defaults() -> dict:
    return {
        "port": 8765,
        "password": new_password(),  # entry password; "" turns it off
        "admin_key": secrets.token_urlsafe(24),
        "hf_token": "",
        "models": list(ai_agent.DEFAULT_MODELS),
        "tavily_key": "",
        "telegram_token": "",
        "admin_telegram_chat_id": "",
        "limits": dict(DEFAULT_PLANS["free"]),
        "global_ai_per_day": DEFAULT_GLOBAL_AI_PER_DAY,
        "tunnel": "quick",  # quick (trycloudflare.com) | token (named Cloudflare tunnel) | off
        "tunnel_token": "",
        "public_url": "",  # with tunnel=token: the hostname set up in Cloudflare, e.g. https://chat.example.com
        "host_agent_cli": "",  # "claude" runs the host's Claude Code as an agent inside the app
        "database_url": "",  # empty: SQLite in the data folder
    }


# Settings the host can change from the admin page (others need a restart or are internal).
EDITABLE = {"password", "hf_token", "models", "tavily_key", "limits", "global_ai_per_day", "host_agent_cli"}
SECRET = {"hf_token", "tavily_key", "telegram_token", "tunnel_token", "admin_key", "database_url"}


class Config:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        base = defaults()
        self._data = {**base, **saved, "limits": {**base["limits"], **saved.get("limits", {})}}
        if saved != self._data:
            self._save()

    @classmethod
    def load(cls, folder: Path | None = None) -> "Config":
        return cls((folder or data_dir()) / "config.json")

    def __getitem__(self, key: str):
        with self._lock:
            return self._data[key]

    def update(self, changes: dict) -> None:
        unknown = set(changes) - EDITABLE
        if unknown:
            raise ValueError(f"바꿀 수 없는 설정: {', '.join(sorted(unknown))}")
        with self._lock:
            for key, value in changes.items():
                self._data[key] = {**self._data["limits"], **value} if key == "limits" else value
            self._save()

    def public_view(self) -> dict:
        """Settings for the admin page; secrets only say whether they are set."""
        with self._lock:
            return {k: (bool(v) if k in SECRET else v) for k, v in self._data.items()}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            self.path.chmod(0o600)  # holds tokens
        except OSError:
            pass
