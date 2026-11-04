"""Plain records passed between the stores, the community logic and the UI."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    room: str
    author: str
    text: str
    ts: float
    is_ai: bool = False
    id: int = 0
    user_id: int | None = None


@dataclass(frozen=True)
class Post:
    id: int
    title: str
    body: str
    author: str
    ts: float
    user_id: int | None = None


@dataclass(frozen=True)
class Comment:
    post_id: int
    author: str
    text: str
    ts: float
    is_ai: bool = False
    id: int = 0
    user_id: int | None = None


@dataclass(frozen=True)
class PersonalAgent:
    name: str
    owner: str
    cli: str
    token_hash: str
    ts: float
    owner_id: int | None = None


@dataclass(frozen=True)
class User:
    id: int
    email: str | None
    nickname: str | None
    role: str = "user"
    plan: str = "free"
    banned: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"
