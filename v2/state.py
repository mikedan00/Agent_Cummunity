"""The one Community per server process, shared by browser sessions, the Telegram bot and the API.

Module state (not st.cache_resource) so the API routes in app.py, which run
outside any Streamlit script, see the same object as the pages.
"""

import json
import os
import threading

import streamlit as st

import ai_agent
import db
from agent_hub import AgentHub
from chat_store import ChatStore
from community import Community, load_agents
from forum_store import ForumStore
from telegram_bot import TelegramBridge

LOCAL_DB = "sqlite:///community.db"  # used when DATABASE_URL is not set

_lock = threading.Lock()
_community: tuple[Community, dict] | None = None


def get_setting(name: str, default=None):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # no secrets.toml present
        pass
    return os.environ.get(name, default)


def parse_models(value) -> list[str]:
    """HF_MODELS from Streamlit Secrets is a list; from an environment variable (e.g. HF Spaces
    secrets) it is a string: a JSON list or comma/newline-separated model ids."""
    if not value:
        return list(ai_agent.DEFAULT_MODELS)
    if isinstance(value, str):
        value = json.loads(value) if value.strip().startswith("[") else value.replace("\n", ",").split(",")
    return [m.strip() for m in value if m.strip()]


def get_community() -> tuple[Community, dict]:
    """Returns (community, status) — status holds the database kind, telegram errors and the bot name."""
    global _community
    with _lock:
        if _community is None:
            _community = _build()
        return _community


def _build() -> tuple[Community, dict]:
    hf_token = get_setting("HF_TOKEN")
    telegram_token = get_setting("TELEGRAM_BOT_TOKEN")
    database_url = get_setting("DATABASE_URL")
    engine = db.make_engine(database_url or LOCAL_DB)
    listeners = []

    def emit(record: dict) -> None:
        for listener in listeners:
            listener(record)

    chat = ChatStore(listener=emit, engine=engine)
    forum = ForumStore(listener=emit, engine=engine)
    hub = AgentHub(listener=emit, engine=engine)
    status = {"database": engine.dialect.name, "database_is_local": not database_url, "engine": engine}

    community = Community(chat, forum, load_agents(), hf_token,
                          parse_models(get_setting("HF_MODELS")),
                          get_setting("TAVILY_API_KEY"), hub=hub)
    if telegram_token:
        try:
            bridge = TelegramBridge(telegram_token, community)
            status["telegram_bot"] = bridge.call("getMe")["username"]  # also checks the token
            listeners.append(bridge.on_record)
            bridge.start()
        except Exception as e:
            status["telegram_error"] = str(e)
    return community, status
