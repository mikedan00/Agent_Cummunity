import pytest
from fastapi.testclient import TestClient

import ai_agent
import db
from config import Config
from server import create_app


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "ask", lambda *a, **kw: "AI 답변")
    monkeypatch.setattr(ai_agent, "forum_reply", lambda *a, **kw: "에이전트 의견")
    config = Config(tmp_path / "config.json")
    config.update({"hf_token": "hf_test", "password": "secret12"})
    app = create_app(config, engine=db.make_engine("sqlite://"))
    app.state.community.background = False
    return app, config


def client(app):
    return TestClient(app)


def join(app, nickname, password="secret12"):
    c = client(app)
    r = c.post("/api/join", json={"nickname": nickname, "password": password})
    assert r.status_code == 200, r.text
    return c


def test_visitor_reads_but_cannot_write(setup):
    app, _ = setup
    c = client(app)
    state = c.get("/api/state").json()
    assert state["me"] is None and state["needs_password"] and state["invite_url"] is None
    assert c.post("/api/rooms/lobby/messages", json={"text": "hi"}).status_code == 401
    assert c.get("/api/rooms/lobby/messages").json() == []


def test_join_needs_password_and_unique_nickname(setup):
    app, _ = setup
    c = client(app)
    assert c.post("/api/join", json={"nickname": "철수", "password": "wrong"}).status_code == 403
    join(app, "철수")
    r = client(app).post("/api/join", json={"nickname": "철수", "password": "secret12"})
    assert r.status_code == 400 and "이미 쓰고" in r.json()["error"]
    assert client(app).post("/api/join", json={"nickname": "Critic", "password": "secret12"}).status_code == 400


def test_session_cookie_keeps_you_in(setup):
    app, _ = setup
    c = join(app, "철수")
    assert c.get("/api/state").json()["me"]["nickname"] == "철수"
    c.post("/api/logout")
    assert c.get("/api/state").json()["me"] is None


def test_chat_with_ai_and_usage(setup):
    app, _ = setup
    c = join(app, "철수")
    assert c.post("/api/rooms/lobby/messages", json={"text": "@AI 안녕"}).status_code == 200
    msgs = c.get("/api/rooms/lobby/messages").json()
    assert [(m["author"], m["text"]) for m in msgs] == [("철수", "@AI 안녕"), ("AI · gemma-4-26B-A4B-it", "AI 답변")]
    assert c.get("/api/state").json()["usage"]["ai_chat"] == [1, 30]


def test_forum_post_comment_and_limits(setup):
    app, config = setup
    config.update({"limits": {"posts_per_day": 1}})
    from server import apply_settings
    apply_settings(app.state.community, config)
    c = join(app, "철수")
    post = c.post("/api/posts", json={"title": "아이디어", "body": "본문"}).json()
    detail = c.get(f"/api/posts/{post['id']}").json()
    assert [x["author"] for x in detail["comments"]] == ["Optimist", "Critic"]
    r = c.post("/api/posts", json={"title": "둘째", "body": "본문"})
    assert r.status_code == 400 and "글쓰기 한도(1개)" in r.json()["error"]
    assert c.post(f"/api/posts/{post['id']}/comments", json={"text": "@Critic 더?"}).status_code == 200
    assert c.get("/api/posts").json()[0]["comments"] == 4
    assert c.get("/api/posts/999").status_code == 404


def test_admin_key_makes_host_admin(setup):
    app, config = setup
    c = client(app)
    assert c.get("/admin?key=wrong", follow_redirects=False).status_code == 403
    assert c.get("/api/admin/settings").status_code == 403
    assert c.get(f"/admin?key={config['admin_key']}", follow_redirects=False).status_code == 303
    settings = c.get("/api/admin/settings").json()
    assert settings["hf_token"] is True and settings["admin_key"] is True  # secrets are not shown
    assert settings["password"] == "secret12"
    state = c.get("/api/state").json()
    assert state["admin"] and state["invite_url"].endswith("/?invite=secret12")
    c.post("/api/join", json={"nickname": "호스트", "password": "secret12"})
    assert c.get("/api/state").json()["me"]["admin"] is True
    assert c.get("/api/admin/qr.svg").text.startswith("<?xml")


def test_admin_changes_settings_live(setup):
    app, config = setup
    c = client(app)
    c.get(f"/admin?key={config['admin_key']}")
    new = c.put("/api/admin/settings", json={"password": "new"}).json()["password"]
    assert new != "secret12" and len(new) == 8
    assert c.put("/api/admin/settings", json={"admin_key": "x"}).status_code == 400  # not editable
    c.put("/api/admin/settings", json={"limits": {"ai_per_day": 1}, "password": ""})
    assert app.state.community.limits.plans["free"]["ai_per_day"] == 1
    assert client(app).post("/api/join", json={"nickname": "누구나"}).status_code == 200  # password off


def test_report_hide_and_ban(setup):
    app, config = setup
    kim, lee = join(app, "철수"), join(app, "영희")
    lee.post("/api/rooms/lobby/messages", json={"text": "광고"})
    msg = kim.get("/api/rooms/lobby/messages").json()[0]
    assert kim.post("/api/report", json={"target": "message", "target_id": msg["id"]}).json() == {"reported": True}
    assert kim.get("/api/admin/reports").status_code == 403
    host = client(app)
    host.get(f"/admin?key={config['admin_key']}")
    assert [r["text"] for r in host.get("/api/admin/reports").json()] == ["광고"]
    assert host.post("/api/admin/ban", json={"target": "message", "target_id": msg["id"]}).status_code == 200
    assert kim.get("/api/rooms/lobby/messages").json() == []
    assert lee.post("/api/rooms/lobby/messages", json={"text": "또"}).status_code == 403


def test_personal_agent_via_connector_api(setup):
    app, _ = setup
    kim = join(app, "철수")
    created = kim.post("/api/my/agents", json={"cli": "claude"}).json()
    assert created["name"] == "철수_claude" and "--token" in created["command"]
    bot = client(app)
    auth = {"Authorization": f"Bearer {created['token']}"}
    assert bot.post("/api/agent/hello", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert bot.post("/api/agent/hello", headers=auth).json()["cli"] == "claude"
    assert bot.get("/api/agent/task?wait=0", headers=auth).json() == {"task": None}

    kim.post("/api/rooms/lobby/messages", json={"text": "@철수_claude 안녕"})
    task = bot.get("/api/agent/task?wait=0", headers=auth).json()["task"]
    assert task["room"] == "lobby"
    assert bot.post("/api/agent/reply", headers=auth, json={"task_id": "t999", "text": "x"}).status_code == 404
    assert bot.post("/api/agent/reply", headers=auth, json={"task_id": task["id"], "text": "반가워요"}).status_code == 200
    assert kim.get("/api/rooms/lobby/messages").json()[-1]["text"] == "반가워요"

    assert [a["name"] for a in kim.get("/api/my/agents").json()] == ["철수_claude"]
    assert join(app, "영희").delete("/api/my/agents/철수_claude").status_code == 404  # not the owner
    assert kim.delete("/api/my/agents/철수_claude").status_code == 200
    assert bot.post("/api/agent/hello", headers=auth).status_code == 401


def test_websocket_receives_new_messages(setup):
    app, _ = setup
    kim = join(app, "철수")
    with kim.websocket_connect("/ws") as ws:
        kim.post("/api/rooms/lobby/messages", json={"text": "실시간"})
        record = ws.receive_json()
        assert (record["type"], record["text"], record["author"]) == ("message", "실시간", "철수")


def test_connector_download_and_cli_list(setup):
    app, _ = setup
    c = client(app)
    assert "Agent Community local connector" in c.get("/download/agent_connector.py").text
    assert set(c.get("/api/clis").json()) == {"claude", "codex", "grok", "agy", "gemini"}


def test_config_defaults_and_persistence(tmp_path):
    a = Config(tmp_path / "c.json")
    assert len(a["password"]) == 8 and a["tunnel"] == "quick" and a["limits"]["ai_per_day"] == 30
    a.update({"hf_token": "x"})
    b = Config(tmp_path / "c.json")
    assert b["admin_key"] == a["admin_key"] and b["hf_token"] == "x"
