"""Browser sessions: joining with a nickname (and the entry password), and who a cookie belongs to.

A session token lives only in the visitor's HttpOnly cookie; the database keeps
its SHA-256 as the account's subject (issuer 'local').
"""

import hashlib
import secrets

from models import User
from users import UserStore

COOKIE = "ac_session"
ADMIN_COOKIE = "ac_admin"
ISSUER = "local"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Sessions:
    def __init__(self, users: UserStore):
        self.users = users

    def join(self, nickname: str, password: str, required_password: str,
             taken: set[str] = frozenset()) -> tuple[User, str]:
        """Create an account for a new visitor. Returns (user, session token)."""
        if required_password and not secrets.compare_digest(password.strip(), required_password):
            raise PermissionError("입장 비밀번호가 맞지 않습니다.")
        nickname = self.users.check_nickname(nickname, taken)
        token = secrets.token_urlsafe(32)
        user = self.users.login(ISSUER, _hash(token), None)
        return self.users.set_nickname(user.id, nickname, taken), token

    def user_for(self, token: str | None) -> User | None:
        return self.users.find(ISSUER, _hash(token)) if token else None
