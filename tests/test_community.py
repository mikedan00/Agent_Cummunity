import json
from types import SimpleNamespace as NS

import pytest

import ai_agent
from chat_store import ChatStore
from community import NO_TOKEN, Community, mentioned_agents
from forum_store import ForumStore
from telegram_bot import TelegramBridge

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

def test_forum_posts_comments_and_restore():
    records = []
    f = ForumStore(listener=records.append)
    p1 = f.create_post(" 아이디어 ", "본문", "kim", now=1)
    p2 = f.create_post("둘째", "본문2", "lee", now=2)
    f.add_comment(p1.id, "Critic", "위험", is_ai=True, now=3)
    assert (p1.id, p1.title, p2.id) == (1, "아이디어", 2)
    assert [p.id for p in f.posts()] == [2, 1]
    with pytest.raises(KeyError):
        f.add_comment(99, "x", "y")
    with pytest.raises(ValueError):
        f.create_post(" ", "body", "kim")

    f2 = ForumStore()
    f2.restore(json.loads(json.dumps(records)) + [{"type": "message", "room": "lobby", "ts": 0}])
    assert [p.title for p in f2.posts()] == ["둘째", "아이디어"]
    assert [(c.author, c.is_ai) for c in f2.comments(1)] == [("Critic", True)]
    assert f2.create_post("셋째", "b", "kim").id == 3  # ids continue after restore


def test_chat_store_ignores_forum_records():
    s = ChatStore()
    s.restore([{"type": "post", "id": 1, "title": "t", "body": "b", "author": "a", "ts": 1}])
    assert s.messages("lobby") == []


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
    listeners = []
    c = make_community(listener=lambda r: [fn(r) for fn in listeners])
    bridge = TelegramBridge("tok", c, http=FakeTelegram())
    listeners.append(bridge.on_record)
    return bridge


def update(chat_id, text, name="민수"):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "from": {"first_name": name}, "text": text}}


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


def test_telegram_call_raises_on_api_error():
    http = NS(post=lambda url, json, timeout: NS(json=lambda: {"ok": False, "description": "Unauthorized"}))
    with pytest.raises(RuntimeError, match="Unauthorized"):
        TelegramBridge("bad", make_community(), http=http).call("getMe")
