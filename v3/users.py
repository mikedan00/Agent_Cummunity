"""User accounts: created on first login (Google OIDC), with a unique nickname picked once."""

import re
import secrets
import threading
import time

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

import db
from models import User

NICKNAME_RE = re.compile(r"^[0-9A-Za-z가-힣_]{2,20}$")
RESERVED = {"ai", "admin", "system", "telegram"}
LINK_CODE_SEC = 600  # a Telegram link code works for 10 minutes


def _user(r) -> User:
    return User(r.id, r.email, r.nickname, r.role, r.plan, r.banned_at is not None)


class UserStore:
    def __init__(self, engine: Engine | None = None, admin_emails: set[str] | None = None):
        self._engine = engine or db.make_engine("sqlite://")
        self._admin_emails = {e.strip().lower() for e in (admin_emails or set()) if e.strip()}
        self._lock = threading.Lock()
        self._link_codes: dict[str, tuple[int, float]] = {}  # code -> (user id, expires at)

    @property
    def engine(self) -> Engine:
        return self._engine

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

    def find(self, issuer: str, subject: str) -> User | None:
        u = db.users.c
        with self._engine.connect() as conn:
            row = conn.execute(select(db.users).where(u.issuer == issuer, u.subject == subject)).first()
        return _user(row) if row else None

    def set_role(self, user_id: int, role: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(db.users).where(db.users.c.id == user_id).values(role=role))

    def check_nickname(self, nickname: str, taken: set[str] = frozenset()) -> str:
        """Return the cleaned nickname, or raise ValueError saying why it can't be used."""
        nickname = nickname.strip()
        if not NICKNAME_RE.match(nickname):
            raise ValueError("닉네임은 2~20자의 한글, 영문, 숫자, _ 만 쓸 수 있습니다.")
        lowered = nickname.lower()
        if lowered in RESERVED or lowered in {t.lower() for t in taken}:
            raise ValueError("사용할 수 없는 닉네임입니다.")
        with self._engine.connect() as conn:
            if conn.scalar(select(func.count()).where(func.lower(db.users.c.nickname) == lowered)):
                raise ValueError("이미 쓰고 있는 닉네임입니다.")
        return nickname

    def set_nickname(self, user_id: int, nickname: str, taken: set[str] = frozenset()) -> User:
        """Set the nickname once. `taken` holds other names it must not clash with (agents)."""
        nickname = self.check_nickname(nickname, taken)
        u = db.users.c
        with self._engine.begin() as conn:
            if conn.scalar(select(u.nickname).where(u.id == user_id)):
                raise ValueError("닉네임은 한 번만 정할 수 있습니다.")
            try:
                conn.execute(update(db.users).where(u.id == user_id).values(nickname=nickname))
            except IntegrityError:
                raise ValueError("이미 쓰고 있는 닉네임입니다.") from None
        return self.get(user_id)

    def set_banned(self, user_id: int, banned: bool) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(db.users).where(db.users.c.id == user_id)
                         .values(banned_at=time.time() if banned else None))

    # ---- Telegram ----

    def issue_link_code(self, user_id: int, now: float | None = None) -> str:
        """A 6-digit code the user sends to the bot as '/link <code>'."""
        now = now if now is not None else time.time()
        with self._lock:
            self._link_codes = {c: v for c, v in self._link_codes.items() if v[1] > now and v[0] != user_id}
            code = f"{secrets.randbelow(10**6):06d}"
            while code in self._link_codes:
                code = f"{secrets.randbelow(10**6):06d}"
            self._link_codes[code] = (user_id, now + LINK_CODE_SEC)
        return code

    def redeem_link_code(self, code: str, telegram_user_id: int, now: float | None = None) -> User | None:
        """Link this Telegram account to the code's user (replacing an earlier link)."""
        now = now if now is not None else time.time()
        with self._lock:
            user_id, expires = self._link_codes.pop(code.strip(), (None, 0))
        if user_id is None or expires <= now:
            return None
        t = db.telegram_links.c
        with self._engine.begin() as conn:
            conn.execute(delete(db.telegram_links).where(t.telegram_user_id == str(telegram_user_id)))
            conn.execute(insert(db.telegram_links).values(telegram_user_id=str(telegram_user_id), user_id=user_id,
                                                          ts=now))
        return self.get(user_id)

    def by_telegram(self, telegram_user_id: int) -> User | None:
        t = db.telegram_links.c
        with self._engine.connect() as conn:
            user_id = conn.scalar(select(t.user_id).where(t.telegram_user_id == str(telegram_user_id)))
        return self.get(user_id) if user_id else None

    def telegram_linked(self, user_id: int) -> bool:
        with self._engine.connect() as conn:
            return bool(conn.scalar(select(func.count()).where(db.telegram_links.c.user_id == user_id)))
