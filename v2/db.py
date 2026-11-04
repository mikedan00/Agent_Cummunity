"""Database schema and engine. PostgreSQL (e.g. Supabase) in production, SQLite locally and in tests.

Tables are created on startup with create_all; there are no migrations yet.
Timestamps are Unix seconds (float), like the rest of the code.
"""

import os

from sqlalchemy import (Boolean, Column, Date, Float, ForeignKey, Index, Integer, MetaData, String, Table,
                        Text, UniqueConstraint, create_engine, event, insert)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("issuer", String(200), nullable=False),
    Column("subject", String(200), nullable=False),
    Column("email", String(320)),
    Column("nickname", String(40), unique=True),  # NULL until the user picks one
    Column("role", String(10), nullable=False, default="user"),
    Column("plan", String(20), nullable=False, default="free"),
    Column("ts", Float, nullable=False),
    Column("banned_at", Float),
    UniqueConstraint("issuer", "subject"),
)

rooms = Table(
    "rooms", metadata,
    Column("name", String(60), primary_key=True),
    Column("ai_name", String(40)),
    Column("created_by", Integer, ForeignKey("users.id")),
    Column("ts", Float, nullable=False),
)

messages = Table(
    "messages", metadata,
    Column("id", Integer, primary_key=True),
    Column("room", String(60), ForeignKey("rooms.name"), nullable=False),
    Column("user_id", Integer, ForeignKey("users.id")),  # NULL for AI messages
    Column("author", String(80), nullable=False),
    Column("text", Text, nullable=False),
    Column("is_ai", Boolean, nullable=False, default=False),
    Column("ts", Float, nullable=False),
    Column("hidden_at", Float),
    Index("ix_messages_room_id", "room", "id"),
)

posts = Table(
    "posts", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id")),
    Column("title", String(200), nullable=False),
    Column("body", Text, nullable=False),
    Column("author", String(80), nullable=False),
    Column("ts", Float, nullable=False),
    Column("hidden_at", Float),
)

comments = Table(
    "comments", metadata,
    Column("id", Integer, primary_key=True),
    Column("post_id", Integer, ForeignKey("posts.id"), nullable=False),
    Column("user_id", Integer, ForeignKey("users.id")),
    Column("author", String(80), nullable=False),
    Column("text", Text, nullable=False),
    Column("is_ai", Boolean, nullable=False, default=False),
    Column("ts", Float, nullable=False),
    Column("hidden_at", Float),
    Index("ix_comments_post_id", "post_id", "id"),
)

personal_agents = Table(
    "personal_agents", metadata,
    Column("name", String(60), primary_key=True),
    Column("owner_id", Integer, ForeignKey("users.id")),
    Column("owner", String(80), nullable=False),  # owner's nickname, for display
    Column("cli", String(20), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("ts", Float, nullable=False),
    Column("revoked_at", Float),
)

agent_tasks = Table(
    "agent_tasks", metadata,
    Column("id", Integer, primary_key=True),
    Column("agent", String(60), ForeignKey("personal_agents.name"), nullable=False),
    Column("kind", String(10), nullable=False),  # chat | forum
    Column("room", String(60)),
    Column("post_id", Integer),
    Column("prompt", Text, nullable=False),
    Column("status", String(10), nullable=False, default="queued"),  # queued | taken | done
    Column("ts", Float, nullable=False),
    Index("ix_agent_tasks_agent_status", "agent", "status", "id"),
)

usage = Table(
    "usage", metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("day", Date, primary_key=True),
    Column("kind", String(20), primary_key=True),
    Column("count", Integer, nullable=False, default=0),
)

daily_totals = Table(  # service-wide counters, e.g. all AI calls today
    "daily_totals", metadata,
    Column("day", Date, primary_key=True),
    Column("kind", String(20), primary_key=True),
    Column("count", Integer, nullable=False, default=0),
)

telegram_links = Table(
    "telegram_links", metadata,
    Column("telegram_user_id", String(30), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("ts", Float, nullable=False),
)

reports = Table(
    "reports", metadata,
    Column("id", Integer, primary_key=True),
    Column("reporter_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("target", String(10), nullable=False),  # message | post | comment
    Column("target_id", Integer, nullable=False),
    Column("ts", Float, nullable=False),
    Column("resolved_at", Float),
)


def add_test_user(engine: Engine, email: str = "kim@example.com", nickname: str | None = None) -> int:
    """Insert a user row and return its id (tests and local tools)."""
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            issuer="test", subject=email, email=email, nickname=nickname, role="user", plan="free",
            ts=0)).inserted_primary_key[0]


def make_engine(url: str | None = None) -> Engine:
    """Engine for `url` (default: DATABASE_URL, else an in-memory SQLite DB) with all tables created."""
    url = url or os.environ.get("DATABASE_URL") or "sqlite://"
    if url.startswith(("postgres://", "postgresql://")):
        url = "postgresql+psycopg://" + url.split("://", 1)[1]  # Supabase gives a plain postgres URL
    if url.startswith("sqlite"):
        # One shared connection for in-memory DBs; allow use from the API and Telegram threads.
        kw = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kw["poolclass"] = StaticPool
        engine = create_engine(url, **kw)
        # SQLite ignores foreign keys unless asked; check them like PostgreSQL does.
        event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    metadata.create_all(engine)
    return engine
