import copy
from datetime import date

import pytest

import ai_agent
from agent_hub import AgentHub
from chat_store import ChatStore
from community import AGENT_LIMITED, AI_LIMITED, GLOBAL_LIMITED, Community
from forum_store import ForumStore
from limits import Limits
from users import UserStore

PLANS = {"free": {"ai_per_day": 2, "posts_per_day": 1, "agent_calls_per_hour": 1, "messages_per_min": 10}}
AGENTS = {"Critic": {"name": "Critic", "persona": "critical"}}


@pytest.fixture
def setup(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_agent, "ask", lambda *a, **kw: calls.append("ask") or "답변")
    monkeypatch.setattr(ai_agent, "forum_reply", lambda *a, **kw: calls.append("forum") or "의견")
    users = UserStore()
    day = {"today": date(2026, 11, 4)}
    capped = []
    limits = Limits(users.engine, plans=copy.deepcopy(PLANS), global_ai_per_day=4, on_global_cap=capped.append,
                    clock=lambda: day["today"])
    engine = users.engine  # one database, so messages can reference their users
    c = Community(ChatStore(engine=engine), ForumStore(engine=engine), AGENTS, "hf", ["org/m"], background=False,
                  hub=AgentHub(engine=engine), users=users, limits=limits)
    kim = users.set_nickname(users.login("g", "1", "kim@x").id, "kim")
    lee = users.set_nickname(users.login("g", "2", "lee@x").id, "lee")
    return c, kim, lee, calls, day, capped


def last_text(c):
    return c.chat.messages("lobby")[-1].text


def test_daily_ai_limit_per_user_resets_next_day(setup):
    c, kim, lee, calls, day, _ = setup
    for _ in range(2):
        c.send_chat("lobby", "kim", "@AI hi", user=kim)
    assert calls == ["ask", "ask"]
    c.send_chat("lobby", "kim", "@AI hi", user=kim)
    assert last_text(c) == AI_LIMITED.format(n=2) and len(calls) == 2  # no AI call over the limit
    c.send_chat("lobby", "lee", "@AI hi", user=lee)  # other users are unaffected
    assert last_text(c) == "답변"
    day["today"] = date(2026, 11, 5)
    c.send_chat("lobby", "kim", "@AI hi", user=kim)
    assert last_text(c) == "답변"


def test_post_limit_and_post_replies_do_not_use_ai_quota(setup):
    c, kim, *_ = setup
    post = c.create_post("T", "B", "kim", user=kim)
    assert [x.text for x in c.forum.comments(post.id)] == ["의견"]
    with pytest.raises(ValueError, match="글쓰기 한도"):
        c.create_post("T2", "B2", "kim", user=kim)
    c.add_comment(post.id, "kim", "@Critic 더?", user=kim)
    c.add_comment(post.id, "kim", "@Critic 또?", user=kim)
    assert [x.text for x in c.forum.comments(post.id) if x.is_ai] == ["의견", "의견", "의견"]


def test_global_cap_stops_ai_for_everyone_and_notifies_once(setup):
    c, kim, lee, calls, _, capped = setup
    for user in (kim, kim, lee, lee):
        c.send_chat("lobby", user.nickname, "@AI hi", user=user)
    assert len(calls) == 4
    c.send_chat("lobby", "lee", "@AI hi", user=lee)
    assert last_text(c) == AI_LIMITED.format(n=2)  # lee hit the personal limit first
    c.limits.plans["free"]["ai_per_day"] = 10
    c.send_chat("lobby", "lee", "@AI hi", user=lee)
    c.send_chat("lobby", "kim", "@AI hi", user=kim)
    assert last_text(c) == GLOBAL_LIMITED and len(calls) == 4
    assert capped == [4]  # admin is told only once a day


def test_global_cap_gives_back_personal_quota(setup):
    c, kim, *_ = setup
    c.limits.global_ai_per_day = 0
    c.send_chat("lobby", "kim", "@AI hi", user=kim)
    assert last_text(c) == GLOBAL_LIMITED
    c.limits.global_ai_per_day = 10
    for _ in range(2):  # both of kim's daily calls are still available
        c.send_chat("lobby", "kim", "@AI hi", user=kim)
        assert last_text(c) == "답변"


def test_message_rate_limit(setup):
    c, kim, *_ = setup
    c.limits.plans["free"]["messages_per_min"] = 3
    for i in range(3):
        c.send_chat("lobby", "kim", f"m{i}", user=kim)
    with pytest.raises(ValueError, match="너무 빨리"):
        c.send_chat("lobby", "kim", "m4", user=kim)
    assert c.limits.allow_message(kim, now=10**12)  # a minute later it works again


def test_personal_agent_hourly_limit(setup):
    c, kim, lee, *_ = setup
    agent, token = c.hub.create("lee", "claude")
    c.hub.authenticate(token)
    c.send_chat("lobby", "kim", f"@{agent.name} 1", user=kim)
    c.send_chat("lobby", "kim", f"@{agent.name} 2", user=kim)
    assert last_text(c) == AGENT_LIMITED.format(n=1)
    assert c.hub.calls_since(agent.name, 0) == 1


def test_concurrent_takes_never_exceed_the_limit(tmp_path):
    import os
    import threading

    import db
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'l.db'}"
    users = UserStore(db.make_engine(url))
    kim = users.login("g", "1", "kim@x")
    limits = Limits(users.engine, plans={"free": dict(PLANS["free"], ai_per_day=5)})
    results = []
    threads = [threading.Thread(target=lambda: results.append(limits.take(kim, "ai_chat"))) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 5
    assert limits.used(kim, "ai_chat") == 5
