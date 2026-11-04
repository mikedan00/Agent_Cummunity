"""Who is using this browser session: Streamlit's built-in Google login (st.login), or a
development login when AUTH_DEV_MODE=1 and no [auth] section is configured."""

import os

import streamlit as st

from models import User
from users import UserStore

GOOGLE = "google"


def login_configured() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:  # no secrets.toml
        return False


def dev_mode() -> bool:
    return os.environ.get("AUTH_DEV_MODE") == "1" and not login_configured()


def current_user(users: UserStore) -> User | None:
    if login_configured():
        if not st.user.get("is_logged_in", False):
            return None
        return users.login(st.user.get("iss") or GOOGLE, st.user.get("sub"), st.user.get("email"))
    if dev_mode() and (email := st.session_state.get("dev_email")):
        return users.login("dev", email, email)
    return None


def login_widget() -> None:
    """Sidebar controls for a visitor who is not logged in."""
    if login_configured():
        st.button("🔑 Google로 로그인", on_click=st.login, args=(GOOGLE,), type="primary")
        st.caption("Space 페이지 안에서 로그인 창이 열리지 않으면 앱 주소(…hf.space)로 직접 접속하세요.")
    elif dev_mode():
        st.error("개발용 로그인 모드 (AUTH_DEV_MODE=1). 배포 환경에서는 끄세요.")
        with st.form("dev_login"):
            email = st.text_input("이메일 (개발용)")
            if st.form_submit_button("로그인") and email.strip():
                st.session_state.dev_email = email.strip()
                st.rerun()
    else:
        st.warning("로그인이 설정되지 않아 읽기만 할 수 있습니다. (Secrets의 [auth] 설정 필요)")


def logout() -> None:
    if login_configured():
        st.logout()
    else:
        st.session_state.pop("dev_email", None)
