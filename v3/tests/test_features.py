from types import SimpleNamespace as NS

import ai_agent
import db
import web_search
from chat_store import ChatStore


# ---- persistence ----

def test_listener_gets_rooms_and_messages_and_data_survives_restart(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path / 'c.db'}")
    records = []
    s = ChatStore(listener=records.append, engine=engine)
    s.create_room("dev")
    s.create_room("dev")  # existing room: no second record
    s.add_message("dev", "kim", "hi", now=10)
    s.add_message("dev", "AI", "hello", is_ai=True, now=11)
    assert [r["type"] for r in records] == ["room", "message", "message"]

    engine.dispose()  # like a server restart: a new engine on the same database
    s2 = ChatStore(engine=db.make_engine(f"sqlite:///{tmp_path / 'c.db'}"))
    assert "dev" in s2.rooms()
    assert [(m.author, m.text, m.is_ai) for m in s2.messages("dev")] == [("kim", "hi", False), ("AI", "hello", True)]


def test_hidden_message_is_not_shown():
    s = ChatStore()
    keep, hide = s.add_message("a", "kim", "keep"), s.add_message("a", "kim", "spam")
    s.hide_message(hide.id)
    assert [m.id for m in s.messages("a")] == [keep.id]


# ---- AI agent tool calling ----

def _resp(content=None, tool_calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls))])


class FakeClient:
    def __init__(self, responses, fail_with_tools=False):
        self.responses = list(responses)
        self.fail_with_tools = fail_with_tools
        self.calls = []

    def chat_completion(self, messages, **kw):
        self.calls.append((list(messages), kw))
        if self.fail_with_tools and "tools" in kw:
            raise RuntimeError("tools not supported")
        return self.responses.pop(0)


def _history():
    from chat_store import Message
    return [Message("a", "kim", "@AI 오늘 서울 날씨?", 1)]


def test_ask_runs_search_tool_and_returns_answer():
    call = NS(id="c1", function=NS(name="web_search", arguments='{"query": "서울 날씨"}'))
    client = FakeClient([_resp(tool_calls=[call]), _resp(content="맑음입니다 [1]")])
    queries = []
    answer = ai_agent.ask("t", "m", _history(), lambda q: queries.append(q) or "[1] weather", client=client)
    assert queries == ["서울 날씨"]
    assert answer.startswith("맑음입니다 [1]")
    assert "🔎 검색: `서울 날씨`" in answer
    second_messages = client.calls[1][0]
    assert second_messages[-1] == {"role": "tool", "tool_call_id": "c1", "content": "[1] weather"}


def test_ask_without_tool_call_answers_directly():
    client = FakeClient([_resp(content="안녕하세요")])
    assert ai_agent.ask("t", "m", _history(), lambda q: "", client=client) == "안녕하세요"


def test_ask_falls_back_when_tools_unsupported():
    client = FakeClient([_resp(content="검색 없이 답변")], fail_with_tools=True)
    assert ai_agent.ask("t", "m", _history(), lambda q: "", client=client) == "검색 없이 답변"
    assert "tools" not in client.calls[-1][1]


def test_system_prompt_has_date():
    from datetime import datetime
    msgs = ai_agent.build_messages([], now=datetime(2026, 11, 3, 23, 5))
    assert "2026-11-03 23:05" in msgs[0]["content"]


# ---- web search ----

def test_search_uses_tavily_then_falls_back(monkeypatch):
    monkeypatch.setattr(web_search, "_tavily", lambda q, k, n: [{"title": "T", "url": "u", "snippet": "s"}])
    monkeypatch.setattr(web_search, "_duckduckgo", lambda q, n: [{"title": "D", "url": "u", "snippet": "s"}])
    assert web_search.search("q", "key")[0]["title"] == "T"
    assert web_search.search("q", None)[0]["title"] == "D"

    def boom(q, k, n):
        raise RuntimeError("down")
    monkeypatch.setattr(web_search, "_tavily", boom)
    assert web_search.search("q", "key")[0]["title"] == "D"


def test_format_results():
    assert web_search.format_results([]) == "No results found."
    out = web_search.format_results([{"title": "T", "url": "https://x", "snippet": "s"}])
    assert out == "[1] T\nhttps://x\ns"


# ---- AI name ----

def test_mention_by_ai_name():
    assert ai_agent.is_ai_mention("모모야 인텔 주가 얼마야?", "모모")
    assert ai_agent.is_ai_mention("@AI 안녕", "모모")
    assert not ai_agent.is_ai_mention("모모야 안녕", None)
    assert not ai_agent.is_ai_mention("안녕 모모", "모모")
    assert ai_agent.is_ai_mention("Momo, hi", "momo") and ai_agent.is_ai_mention("MOMO hi", "Momo")


def test_ai_name_is_per_room_and_saved():
    import pytest
    records = []
    s = ChatStore(listener=records.append)
    assert s.set_ai_name("a", "  모모 ") == "모모"
    assert s.ai_name("a") == "모모" and s.ai_name("b") is None
    with pytest.raises(ValueError):
        s.set_ai_name("a", "@AI")
    assert records[-1] == {"type": "ai_name", "room": "a", "name": "모모", "ts": records[-1]["ts"]}


def test_ask_set_ai_name_tool_renames_without_search_tag():
    call = NS(id="c1", function=NS(name="set_ai_name", arguments='{"name": "모모"}'))
    client = FakeClient([_resp(tool_calls=[call]), _resp(content="네, 이제 모모예요!")])
    names, searched = [], []
    answer = ai_agent.ask("t", "m", _history(), searched.append, set_name=lambda n: names.append(n) or n,
                          client=client)
    assert names == ["모모"] and searched == []
    assert answer == "네, 이제 모모예요!"
    assert client.calls[1][0][-1]["content"] == "Name changed to 모모."


def test_system_prompt_has_ai_name():
    assert "Your name in this room: 모모." in ai_agent.build_messages([], ai_name="모모")[0]["content"]
    assert "Your name in this room: AI." in ai_agent.build_messages([])[0]["content"]
