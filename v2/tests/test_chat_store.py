import pytest

import ai_agent
from chat_store import DEFAULT_ROOM, ChatStore, Message


def test_default_room_exists():
    assert ChatStore().rooms() == [DEFAULT_ROOM]


def test_create_room_strips_and_rejects_empty():
    s = ChatStore()
    assert s.create_room("  dev  ") == "dev"
    assert "dev" in s.rooms()
    with pytest.raises(ValueError):
        s.create_room("   ")


def test_messages_are_per_room_and_capped():
    s = ChatStore(max_messages=3)
    for i in range(5):
        s.add_message("a", "kim", str(i))
    s.add_message("b", "lee", "hi")
    assert [m.text for m in s.messages("a")] == ["2", "3", "4"]
    assert [m.text for m in s.messages("b")] == ["hi"]


def test_presence_expires_and_follows_room_switch():
    s = ChatStore(presence_timeout=10)
    s.touch("a", "s1", "kim", now=100)
    s.touch("a", "s2", "lee", now=100)
    assert s.online("a", now=105) == ["kim", "lee"]
    s.touch("b", "s1", "kim", now=106)  # kim moves to room b
    assert s.online("a", now=106) == ["lee"]
    assert s.online("b", now=106) == ["kim"]
    assert s.online("a", now=120) == []


def test_ai_mention():
    assert ai_agent.is_ai_mention("@AI 안녕")
    assert ai_agent.is_ai_mention("  @ai hello")
    assert not ai_agent.is_ai_mention("hello @AI")


def test_split_provider():
    assert ai_agent.split_provider("google/gemma-4-26B-A4B-it:deepinfra") == ("google/gemma-4-26B-A4B-it", "deepinfra")
    assert ai_agent.split_provider("Qwen/Qwen3.6-27B") == ("Qwen/Qwen3.6-27B", None)


def test_build_messages_roles_and_limit():
    history = [
        Message("a", "kim", "@AI 질문", 1),
        Message("a", "AI", "답변", 2, is_ai=True),
        Message("a", "lee", "고마워", 3),
    ]
    msgs = ai_agent.build_messages(history, limit=2)
    assert msgs[0]["role"] == "system"
    assert msgs[1:] == [
        {"role": "assistant", "content": "답변"},
        {"role": "user", "content": "lee: 고마워"},
    ]
