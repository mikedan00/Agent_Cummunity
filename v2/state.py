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
from limits import DEFAULT_GLOBAL_AI_PER_DAY, DEFAULT_PLANS, Limits
from telegram_bot import TelegramBridge
from users import UserStore

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


def read_plans() -> dict:
    """The free plan's limits, each overridable by a LIMIT_* setting."""
    free = dict(DEFAULT_PLANS["free"])
    for key, setting in [("ai_per_day", "LIMIT_AI_PER_DAY"), ("posts_per_day", "LIMIT_POSTS_PER_DAY"),
                         ("agent_calls_per_hour", "LIMIT_AGENT_CALLS_PER_HOUR"),
                         ("messages_per_min", "LIMIT_MESSAGES_PER_MIN")]:
        if value := get_setting(setting):
            free[key] = int(value)
    return {"free": free}


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
    users = UserStore(engine, admin_emails=set((get_setting("ADMIN_EMAILS") or "").split(",")))
    status = {"database": engine.dialect.name, "database_is_local": not database_url, "engine": engine}

    bridge = None
    admin_chat = get_setting("ADMIN_TELEGRAM_CHAT_ID")

    def notify_cap(cap: int) -> None:
        if bridge and admin_chat:
            bridge.send(int(admin_chat), f"⚠️ 오늘 서비스 전체 AI 호출이 한도({cap}회)에 도달해 AI 답변이 멈췄습니다.")

    limits = Limits(engine, plans=read_plans(), on_global_cap=notify_cap,
                    global_ai_per_day=int(get_setting("GLOBAL_AI_PER_DAY") or DEFAULT_GLOBAL_AI_PER_DAY))
    community = Community(chat, forum, load_agents(), hf_token,
                          parse_models(get_setting("HF_MODELS")),
                          get_setting("TAVILY_API_KEY"), hub=hub, users=users, limits=limits)
    if telegram_token:
        try:
            bridge = TelegramBridge(telegram_token, community)
            status["telegram_bot"] = bridge.call("getMe")["username"]  # also checks the token
            listeners.append(bridge.on_record)
            bridge.start()
        except Exception as e:
            bridge = None
            status["telegram_error"] = str(e)
    return community, status
