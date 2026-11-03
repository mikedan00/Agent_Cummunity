import pytest
from fastapi.testclient import TestClient

from app import agents
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # demo mode, no API calls
    with TestClient(app) as c:
        yield c


def test_new_post_gets_comment_from_every_agent(client):
    r = client.post("/posts", data={"title": "T", "body": "B", "author": "kim"})
    assert r.status_code == 200 and r.url.path == "/posts/1"
    for name in agents.load_agents():
        assert name in r.text
    assert "demo mode" in r.text


def test_mention_triggers_only_that_agent(client):
    client.post("/posts", data={"title": "T", "body": "B", "author": "kim"})
    from app import db
    before = len(db.list_comments(1))
    client.post("/posts/1/comments", data={"body": "@Critic 어떻게 생각해?", "author": "lee"})
    new = db.list_comments(1)[before:]
    assert [c["author"] for c in new] == ["lee", "Critic"]


def test_comment_without_mention_gets_no_agent_reply(client):
    client.post("/posts", data={"title": "T", "body": "B", "author": "kim"})
    from app import db
    before = len(db.list_comments(1))
    client.post("/posts/1/comments", data={"body": "그냥 의견", "author": "lee"})
    assert [c["author"] for c in db.list_comments(1)[before:]] == ["lee"]


def test_missing_post_404(client):
    assert client.get("/posts/999").status_code == 404
    assert client.post("/posts/999/comments", data={"body": "x", "author": "y"}).status_code == 404


def test_mentioned_agents_ignores_unknown_and_dupes():
    known = {"Critic": {}, "Optimist": {}}
    assert agents.mentioned_agents("@Critic @Nobody @Critic @Optimist", known) == ["Critic", "Optimist"]
