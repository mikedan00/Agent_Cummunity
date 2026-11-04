"""The one Community per server process, shared by browser sessions, the Telegram bot and the API.

Module state (not st.cache_resource) so the API routes in app.py, which run
outside any Streamlit script, see the same object as the pages.
"""

import os
import threading

import streamlit as st

import ai_agent
from agent_hub import AgentHub
from chat_store import ChatStore
from community import Community, load_agents
from forum_store import ForumStore
from history import HFHistory
from telegram_bot import TelegramBridge

_lock = threading.Lock()
_community: tuple[Community, dict] | None = None


def get_setting(name: str, default=None):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # no secrets.toml present
        pass
    return os.environ.get(name, default)


def get_community() -> tuple[Community, dict]:
    """Returns (community, status) — status holds history/telegram errors and the bot name."""
    global _community
    with _lock:
        if _community is None:
            _community = _build()
        return _community


def _build() -> tuple[Community, dict]:
    hf_token = get_setting("HF_TOKEN")
    dataset_repo = get_setting("HF_DATASET_REPO")
    telegram_token = get_setting("TELEGRAM_BOT_TOKEN")
    listeners = []

    def emit(record: dict) -> None:
        for listener in listeners:
            listener(record)

    chat, forum, hub = ChatStore(listener=emit), ForumStore(listener=emit), AgentHub(listener=emit)
    status = {"dataset_repo": dataset_repo if hf_token else None}
    if hf_token and dataset_repo:
        try:
            history = HFHistory(dataset_repo, hf_token)
            records = history.load()
            for store in (chat, forum, hub):
                store.restore(records)
            listeners.append(history.append)
        except Exception as e:
            status["history_error"] = str(e)

    community = Community(chat, forum, load_agents(), hf_token,
                          list(get_setting("HF_MODELS") or ai_agent.DEFAULT_MODELS),
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
