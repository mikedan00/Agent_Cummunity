"""User accounts: created on first login (Google OIDC), with a unique nickname picked once."""

import re
import time

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

import db
from models import User

NICKNAME_RE = re.compile(r"^[0-9A-Za-z가-힣_]{2,20}$")
RESERVED = {"ai", "admin", "system", "telegram"}


def _user(r) -> User:
    return User(r.id, r.email, r.nickname, r.role, r.plan, r.banned_at is not None)


class UserStore:
    def __init__(self, engine: Engine | None = None, admin_emails: set[str] | None = None):
        self._engine = engine or db.make_engine("sqlite://")
        self._admin_emails = {e.strip().lower() for e in (admin_emails or set()) if e.strip()}

    def login(self, issuer: str, subject: str, email: str | None) -> User:
        """Find or create the account for this identity; refresh email and admin role."""
        u = db.users.c
        role = "admin" if email and email.lower() in self._admin_emails else None
        with self._engine.begin() as conn:
            row = conn.execute(select(db.users).where(u.issuer == issuer, u.subject == subject)).first()
            if row is None:
                try:
                    conn.execute(insert(db.users).values(issuer=issuer, subject=subject, email=email,
                                                         role=role or "user", plan="free", ts=time.time()))
                except IntegrityError:  # created by a concurrent login
                    pass
            else:
                values = {"email": email} | ({"role": role} if role else {})
                conn.execute(update(db.users).where(u.id == row.id).values(**values))
            row = conn.execute(select(db.users).where(u.issuer == issuer, u.subject == subject)).one()
        return _user(row)

    def get(self, user_id: int) -> User | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(db.users).where(db.users.c.id == user_id)).first()
        return _user(row) if row else None

    def set_nickname(self, user_id: int, nickname: str, taken: set[str] = frozenset()) -> User:
        """Set the nickname once. `taken` holds other names it must not clash with (agents)."""
        nickname = nickname.strip()
        if not NICKNAME_RE.match(nickname):
            raise ValueError("닉네임은 2~20자의 한글, 영문, 숫자, _ 만 쓸 수 있습니다.")
        lowered = nickname.lower()
        if lowered in RESERVED or lowered in {t.lower() for t in taken}:
            raise ValueError("사용할 수 없는 닉네임입니다.")
        u = db.users.c
        with self._engine.begin() as conn:
            if conn.scalar(select(u.nickname).where(u.id == user_id)):
                raise ValueError("닉네임은 한 번만 정할 수 있습니다.")
            if conn.scalar(select(func.count()).where(func.lower(u.nickname) == lowered)):
                raise ValueError("이미 쓰고 있는 닉네임입니다.")
            try:
                conn.execute(update(db.users).where(u.id == user_id).values(nickname=nickname))
            except IntegrityError:
                raise ValueError("이미 쓰고 있는 닉네임입니다.") from None
        return self.get(user_id)

    def set_banned(self, user_id: int, banned: bool) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(db.users).where(db.users.c.id == user_id)
                         .values(banned_at=time.time() if banned else None))
