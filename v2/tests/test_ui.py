"""Drive ui.py with Streamlit's AppTest, using the development login (AUTH_DEV_MODE=1)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import state

UI = str(Path(__file__).parent.parent / "ui.py")


def by_label(widgets, label):
    return next(w for w in widgets if w.label == label)


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_DEV_MODE", "1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ui.db'}")
    for name in ("HF_TOKEN", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(state, "_community", None)  # fresh community for this test
    return AppTest.from_file(UI, default_timeout=30).run()


def login(at, email, nickname):
    by_label(at.text_input, "이메일 (개발용)").input(email)
    by_label(at.button, "로그인").click().run()
    by_label(at.text_input, "닉네임 (2~20자: 한글, 영문, 숫자, _)").input(nickname)
    by_label(at.button, "정하기").click().run()


def test_visitor_reads_only(app):
    assert not app.exception
    assert len(app.chat_input) == 0
    assert any("읽기 전용" in i.value for i in app.info)


def test_login_pick_nickname_then_chat_and_post(app):
    login(app, "kim@x.com", "철수")
    assert not app.exception
    assert any("철수" in c.value for c in app.caption)
    app.chat_input[0].set_value("안녕하세요").run()
    assert any(m.value == "안녕하세요" for m in app.markdown)

    by_label(app.text_input, "제목").input("아이디어")
    by_label(app.text_area, "내용").input("본문")
    by_label(app.button, "등록").click().run()
    post = state.get_community()[0].forum.posts()[0]
    assert (post.title, post.author, post.user_id is not None) == ("아이디어", "철수", True)


def test_create_and_revoke_personal_agent(app):
    login(app, "kim@x.com", "철수")
    by_label(app.button, "@철수_claude 에이전트 만들기").click().run()
    assert any("--token" in c.value for c in app.code)
    hub = state.get_community()[0].hub
    assert [a.name for a in hub.agents()] == ["철수_claude"]
    by_label(app.button, "폐기").click().run()
    assert hub.agents() == []


def test_google_login_button_when_auth_is_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ui.db'}")
    monkeypatch.setattr(state, "_community", None)
    at = AppTest.from_file(UI, default_timeout=30)
    at.secrets["auth"] = {"redirect_uri": "http://localhost:8501/oauth2callback", "cookie_secret": "x" * 32,
                          "google": {"client_id": "id", "client_secret": "secret",
                                     "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration"}}
    at.run()
    assert not at.exception
    assert [b.label for b in at.button if "Google" in b.label] == ["🔑 Google로 로그인"]
    assert len(at.chat_input) == 0
