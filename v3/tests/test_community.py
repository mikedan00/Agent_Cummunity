from types import SimpleNamespace as NS

import pytest

import ai_agent
import db
from chat_store import ChatStore
from community import NO_TOKEN, Community, mentioned_agents
from forum_store import ForumStore
from agent_hub import AgentHub
from telegram_bot import LINK_FIRST, TelegramBridge
from users import UserStore

AGENTS = {"Optimist": {"name": "Optimist", "persona": "positive"},
          "Critic": {"name": "Critic", "persona": "critical"}}


def make_community(token="t", listener=None):
    return Community(ChatStore(listener=listener), ForumStore(listener=listener), AGENTS, token,
                     ["org/model-x:prov"], background=False)


@pytest.fixture
def fake_ai(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_agent, "ask", lambda token, model, history, search, **kw: calls.append(model) or "답변")
    monkeypatch.setattr(ai_agent, "forum_reply",
                        lambda token, model, persona, thread, search: calls.append(persona) or f"{persona} 의견")
    return calls


# ---- forum store ----

def test_forum_posts_comments_and_persistence(tmp_path):
    url = f"sqlite:///{tmp_path / 'f.db'}"
    records = []
    f = ForumStore(listener=records.append, engine=db.make_engine(url))
    p1 = f.create_post(" 아이디어 ", "본문", "kim", now=1)
    p2 = f.create_post("둘째", "본문2", "lee", now=2)
    f.add_comment(p1.id, "Critic", "위험", is_ai=True, now=3)
    assert (p1.id, p1.title, p2.id) == (1, "아이디어", 2)
    assert [p.id for p in f.posts()] == [2, 1]
    assert [r["type"] for r in records] == ["post", "post", "comment"]
    with pytest.raises(KeyError):
        f.add_comment(99, "x", "y")
    with pytest.raises(ValueError):
        f.create_post(" ", "body", "kim")

    f2 = ForumStore(engine=db.make_engine(url))  # like a server restart
    assert [p.title for p in f2.posts()] == ["둘째", "아이디어"]
    assert [(c.author, c.is_ai) for c in f2.comments(1)] == [("Critic", True)]
    assert f2.create_post("셋째", "b", "kim").id == 3


def test_hidden_posts_and_comments_disappear():
    f = ForumStore()
    p = f.create_post("T", "B", "kim")
    c = f.add_comment(p.id, "lee", "spam")
    f.hide_comment(c.id)
    assert f.comments(p.id) == []
    f.hide_post(p.id)
    assert f.posts() == [] and f.post(p.id) is None
    with pytest.raises(KeyError):
        f.add_comment(p.id, "lee", "x")  # hidden posts take no comments


# ---- community ----

def test_new_post_gets_comment_from_every_agent(fake_ai):
    c = make_community()
    post = c.create_post("T", "B", "kim")
    assert [(x.author, x.text, x.is_ai) for x in c.forum.comments(post.id)] == [
        ("Optimist", "positive 의견", True), ("Critic", "critical 의견", True)]


def test_comment_mention_calls_only_that_agent(fake_ai):
    c = make_community()
    post = c.create_post("T", "B", "kim")
    fake_ai.clear()
    c.add_comment(post.id, "lee", "@Critic 어때? @Nobody")
    assert fake_ai == ["critical"]
    c.add_comment(post.id, "lee", "그냥 의견")
    assert [x.author for x in c.forum.comments(post.id)][-3:] == ["lee", "Critic", "lee"]


def test_thread_text_includes_post_and_comments():
    c = make_community(token=None)
    post = c.forum.create_post("제목", "본문", "kim")
    c.forum.add_comment(post.id, "lee", "댓글")
    assert c.thread_text(post.id) == "[Post by kim] 제목\n본문\n\n[Comment by lee] 댓글"


def test_no_token_posts_notice_instead_of_calling_ai(fake_ai):
    c = make_community(token=None)
    post = c.create_post("T", "B", "kim")
    assert {x.text for x in c.forum.comments(post.id)} == {NO_TOKEN}
    c.send_chat("lobby", "kim", "@AI 안녕")
    assert c.chat.messages("lobby")[-1].text == NO_TOKEN
    assert fake_ai == []


def test_chat_ai_answers_mentions_only(fake_ai):
    c = make_community()
    c.send_chat("lobby", "kim", "안녕")
    c.send_chat("lobby", "kim", "@AI 질문", model="org/other")
    msgs = c.chat.messages("lobby")
    assert [m.author for m in msgs] == ["kim", "kim", "AI · other"]
    assert fake_ai == ["org/other"]


def test_mentioned_agents_ignores_unknown_and_dupes():
    assert mentioned_agents("@Critic @Nobody @Critic @Optimist", AGENTS) == ["Critic", "Optimist"]


def test_forum_reply_sends_persona_and_thread():
    client = NS(calls=[])
    client.chat_completion = lambda messages, **kw: client.calls.append(messages) or NS(
        choices=[NS(message=NS(content="좋아요", tool_calls=None))])
    assert ai_agent.forum_reply("t", "m", "You are Critic.", "[Post by kim] T", lambda q: "", client=client) == "좋아요"
    system, user = client.calls[0]
    assert system["content"].startswith("You are Critic.") and "[Post by kim] T" in user["content"]


# ---- telegram ----

class FakeTelegram:
    def __init__(self):
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url.rsplit("/", 1)[-1], json))
        return NS(json=lambda: {"ok": True, "result": {"username": "bot"}})


def make_bridge():
    """A bridge whose Telegram user 1001 is linked to the web account '민수'."""
    listeners = []
    engine = db.make_engine("sqlite://")
    emit = lambda r: [fn(r) for fn in listeners]  # noqa: E731
    users = UserStore(engine)
    c = Community(ChatStore(listener=emit, engine=engine), ForumStore(listener=emit, engine=engine), AGENTS, "t",
                  ["org/model-x:prov"], background=False, hub=AgentHub(engine=engine), users=users)
    minsu = users.set_nickname(users.login("g", "1", "minsu@x").id, "민수")
    assert users.redeem_link_code(users.issue_link_code(minsu.id), 1001)
    bridge = TelegramBridge("tok", c, http=FakeTelegram())
    listeners.append(bridge.on_record)
    return bridge


def update(chat_id, text, user_id=1001):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "from": {"id": user_id, "first_name": "x"},
                                        "text": text}}


def outbox(bridge):
    items = []
    while not bridge._outbox.empty():
        items.append(bridge._outbox.get())
    return items


def test_telegram_chat_goes_to_room_and_room_comes_back(fake_ai):
    b = make_bridge()
    b.handle_update(update(10, "안녕"))
    b.handle_update(update(20, "/room dev"))
    outbox(b)
    b.handle_update(update(10, "@AI 질문"))
    msgs = b.community.chat.messages("lobby")
    assert [(m.author, m.text) for m in msgs] == [("민수 (TG)", "안녕"), ("민수 (TG)", "@AI 질문"),
                                                  ("AI · model-x", "답변")]
    # The AI answer goes back to chat 10, but chat 10's own message is not echoed; chat 20 is in another room.
    assert outbox(b) == [(10, "AI · model-x: 답변")]

    b.community.send_chat("dev", "웹사용자", "반가워")  # message from a browser
    assert outbox(b) == [(20, "웹사용자: 반가워")]


def test_telegram_post_and_comment(fake_ai):
    b = make_bridge()
    b.handle_update(update(10, "/post 앱 아이디어 | 학생용 수학 튜터"))
    post = b.community.forum.posts()[0]
    assert (post.title, post.body, post.author) == ("앱 아이디어", "학생용 수학 튜터", "민수 (TG)")
    sent = [text for _, text in outbox(b)]
    assert sent[0].startswith("글 #1")
    assert sent[1:] == ["[#1] Optimist: positive 의견", "[#1] Critic: critical 의견"]

    b.handle_update(update(10, "/comment 1 @Critic 더 자세히"))
    assert [text for _, text in outbox(b)] == ["[#1] Critic: critical 의견"]  # own comment not echoed

    b.handle_update(update(10, "/posts"))
    assert outbox(b) == [(10, "#1 앱 아이디어 — 민수 (TG) (댓글 4)")]


def test_telegram_usage_errors():
    b = make_bridge()
    b.handle_update(update(10, "/post 제목만"))
    b.handle_update(update(10, "/comment abc"))
    b.handle_update(update(10, "/comment 5 내용"))
    b.handle_update(update(10, "/help@MyBot"))
    texts = [t for _, t in outbox(b)]
    assert texts[:3] == ["사용법: /post 제목 | 내용", "사용법: /comment 번호 내용", "글 #5이 없습니다."]
    assert texts[3].startswith("🤖 Agent Community 봇")


def test_unlinked_telegram_user_can_only_read_until_linking(fake_ai):
    b = make_bridge()
    users = b.community.users
    b.handle_update(update(30, "안녕", user_id=2002))
    b.handle_update(update(30, "/post T | B", user_id=2002))
    assert [t for _, t in outbox(b)] == [LINK_FIRST, LINK_FIRST]
    assert b.community.chat.messages("lobby") == [] and b.community.forum.posts() == []

    b.handle_update(update(30, "/link 000000", user_id=2002))
    assert "코드가 맞지 않거나" in outbox(b)[0][1]
    lee = users.set_nickname(users.login("g", "2", "lee@x").id, "영희")
    b.handle_update(update(30, f"/link {users.issue_link_code(lee.id)}", user_id=2002))
    assert "영희" in outbox(b)[0][1]
    b.handle_update(update(30, "안녕", user_id=2002))
    assert b.community.chat.messages("lobby")[-1].author == "영희 (TG)"
    assert b.community.chat.messages("lobby")[-1].user_id == lee.id


def test_link_code_expires_and_works_once():
    users = UserStore()
    kim = users.set_nickname(users.login("g", "1", "kim@x").id, "kim")
    code = users.issue_link_code(kim.id, now=0)
    assert users.redeem_link_code(code, 5, now=601) is None  # after 10 minutes
    code = users.issue_link_code(kim.id)
    assert users.redeem_link_code(code, 5).id == kim.id
    assert users.redeem_link_code(code, 6) is None  # only once
    assert users.by_telegram(5).id == kim.id and users.by_telegram(6) is None
    assert users.telegram_linked(kim.id)


def test_telegram_writes_count_against_limits(fake_ai):
    b = make_bridge()
    b.community.limits.plans = {"free": dict(b.community.limits.plans["free"], posts_per_day=1)}
    b.handle_update(update(10, "/post 하나 | 본문"))
    outbox(b)
    b.handle_update(update(10, "/post 둘 | 본문"))
    assert [t for _, t in outbox(b)] == ["오늘 글쓰기 한도(1개)를 다 썼습니다. 내일 다시 올려 주세요."]


def test_telegram_call_raises_on_api_error():
    http = NS(post=lambda url, json, timeout: NS(json=lambda: {"ok": False, "description": "Unauthorized"}))
    with pytest.raises(RuntimeError, match="Unauthorized"):
        TelegramBridge("bad", make_community(), http=http).call("getMe")


def test_parse_models_accepts_list_or_env_string():
    from config import parse_models
    assert parse_models(None) == ai_agent.DEFAULT_MODELS
    assert parse_models(["a/b", " c/d "]) == ["a/b", "c/d"]
    assert parse_models('["a/b:prov", "c/d"]') == ["a/b:prov", "c/d"]
    assert parse_models("a/b, c/d\ne/f") == ["a/b", "c/d", "e/f"]
