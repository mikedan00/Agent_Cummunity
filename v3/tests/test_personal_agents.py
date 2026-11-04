import json
import os
import stat
import sys

import pytest

import agent_connector
import db
from agent_hub import AgentHub
from chat_store import ChatStore
from community import OFFLINE, Community
from forum_store import ForumStore
from telegram_bot import TelegramBridge


def make_community(listener=None):
    hub = AgentHub(listener=listener)
    c = Community(ChatStore(listener=listener), ForumStore(listener=listener), {}, None, ["org/m"],
                  background=False, hub=hub)
    return c


# ---- hub ----

def test_create_names_are_unique_and_tokens_authenticate():
    records = []
    hub = AgentHub(listener=records.append)
    a1, t1 = hub.create("철수 김", "claude")
    a2, t2 = hub.create("철수 김", "claude")
    assert (a1.name, a2.name) == ("철수김_claude", "철수김_claude2")
    assert hub.authenticate(t1) == a1 and hub.authenticate(t2) == a2
    assert hub.authenticate("wrong") is None and hub.authenticate("") is None
    assert t1 not in json.dumps(records)  # only the hash is saved
    with pytest.raises(ValueError):
        hub.create("kim", "unknown-cli")



def test_revoked_token_stops_working_and_name_is_not_reused():
    hub = AgentHub()
    kim, lee = db.add_test_user(hub._engine, "kim@x"), db.add_test_user(hub._engine, "lee@x")
    agent, token = hub.create("kim", "claude", owner_id=kim)
    assert not hub.revoke(agent.name, owner_id=lee)  # not the owner
    assert hub.revoke(agent.name, owner_id=kim)
    assert hub.authenticate(token) is None and hub.agents() == []
    assert hub.create("kim", "claude")[0].name == "kim_claude2"


def test_agents_by_owner():
    hub = AgentHub()
    kim, lee = db.add_test_user(hub._engine, "kim@x"), db.add_test_user(hub._engine, "lee@x")
    hub.create("kim", "claude", owner_id=kim)
    hub.create("lee", "codex", owner_id=lee)
    assert [a.name for a in hub.agents(owner_id=lee)] == ["lee_codex"]


def test_tasks_queue_wait_and_complete_only_by_owner():
    hub = AgentHub()
    a = hub.create("a", "claude")[0].name
    b = hub.create("b", "claude")[0].name
    assert hub.next_task(a, timeout=0) is None
    task = hub.enqueue(a, {"kind": "chat", "room": "lobby", "prompt": "p"})
    assert hub.next_task(b, timeout=0) is None
    assert hub.next_task(a, timeout=0) == task
    assert hub.next_task(a, timeout=0) is None  # taken only once
    assert hub.complete(b, task["id"]) is None  # another agent can't answer it
    assert hub.complete(a, "bogus") is None
    assert hub.complete(a, task["id"])["room"] == "lobby"
    assert hub.complete(a, task["id"]) is None  # only once
    assert hub.calls_since(a, 0) == 1


def test_waiting_connector_wakes_up_when_a_task_arrives():
    import threading
    import time
    hub = AgentHub()
    name = hub.create("kim", "claude")[0].name
    got = []
    waiter = threading.Thread(target=lambda: got.append(hub.next_task(name, timeout=5)))
    waiter.start()
    time.sleep(0.2)
    start = time.monotonic()
    hub.enqueue(name, {"kind": "forum", "post_id": 3, "prompt": "p"})
    waiter.join(5)
    assert got and got[0]["post_id"] == 3 and time.monotonic() - start < 2


def test_online_follows_last_seen():
    hub = AgentHub()
    agent, token = hub.create("kim", "codex")
    assert not hub.is_online(agent.name)
    hub.authenticate(token)
    assert hub.is_online(agent.name)
    assert not hub.is_online(agent.name, now=10**12)


# ---- community ----

def test_mention_of_offline_agent_posts_notice():
    c = make_community()
    agent, _ = c.hub.create("kim", "claude")
    c.send_chat("lobby", "lee", f"@{agent.name} 안녕")
    assert [(m.author, m.text, m.is_ai) for m in c.chat.messages("lobby")][-1] == (agent.name, OFFLINE, True)


def test_chat_mention_queues_task_and_reply_is_posted():
    c = make_community()
    agent, token = c.hub.create("kim", "claude")
    c.hub.authenticate(token)
    c.send_chat("lobby", "lee", f"@{agent.name} 이 아이디어 어때?")
    task = c.hub.next_task(agent.name, timeout=0)
    assert task["kind"] == "chat"
    assert f"personal AI agent of kim" in task["prompt"] and f"lee: @{agent.name} 이 아이디어 어때?" in task["prompt"]
    assert c.finish_personal(agent, task["id"], " 좋아요 ")
    assert c.chat.messages("lobby")[-1].text == "좋아요"
    assert not c.finish_personal(agent, task["id"], "again")
    assert c.hub.next_task(agent.name, timeout=0) is None  # the agent's own reply calls nobody


def test_forum_mention_queues_task_with_thread():
    c = make_community()
    agent, token = c.hub.create("kim", "codex")
    c.hub.authenticate(token)
    post = c.forum.create_post("T", "B", "lee")
    c.add_comment(post.id, "lee", f"@{agent.name} 의견?")
    task = c.hub.next_task(agent.name, timeout=0)
    assert task["post_id"] == post.id and "[Post by lee] T" in task["prompt"]
    c.finish_personal(agent, task["id"], "의견입니다")
    assert [(x.author, x.is_ai) for x in c.forum.comments(post.id)][-1] == (agent.name, True)


# ---- connector ----

def fake_cli(tmp_path, name, script):
    """An executable that runs `script` with Python (stands in for a real CLI)."""
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.mark.skipif(os.name == "nt", reason="uses a shebang script")
def test_run_cli_claude_reads_prompt_from_stdin_in_empty_folder(tmp_path):
    path = fake_cli(tmp_path, "claude", "import os, sys\n"
                    "print('args=' + ' '.join(repr(a) for a in sys.argv[1:]))\n"
                    "print('files=' + str(os.listdir('.')))\nprint('prompt=' + sys.stdin.read())")
    out = agent_connector.run_cli("claude", path, "안녕?")
    assert "'--tools' ''" in out and "--strict-mcp-config" in out
    assert "files=[]" in out and "prompt=안녕?" in out


@pytest.mark.skipif(os.name == "nt", reason="uses a shebang script")
def test_run_cli_codex_uses_last_message_file(tmp_path):
    path = fake_cli(tmp_path, "codex", "import sys\nargs = sys.argv[1:]\n"
                    "assert args[:2] == ['exec', '--sandbox'] and args[2] == 'read-only'\n"
                    "open(args[args.index('--output-last-message') + 1], 'w').write('최종 답변')\nprint('log noise')")
    assert agent_connector.run_cli("codex", path, "q") == "최종 답변"


@pytest.mark.skipif(os.name == "nt", reason="uses a shebang script")
def test_run_cli_reports_failure_and_timeout(tmp_path):
    failing = fake_cli(tmp_path, "grok", "import sys\nsys.stderr.write('not logged in')\nsys.exit(1)")
    assert agent_connector.run_cli("grok", failing, "q") == "(답변 생성 실패: not logged in)"
    slow = fake_cli(tmp_path, "agy", "import time\ntime.sleep(5)")
    assert agent_connector.run_cli("agy", slow, "q", timeout=0.5).startswith("(시간 초과")


def test_every_cli_has_install_login_and_command():
    for cli, spec in agent_connector.CLIS.items():
        assert spec["install"]["posix"] and spec["docs"].startswith("https://")
        argv, stdin = agent_connector.build_command(cli, "/bin/x", "PROMPT", "/tmp/o")
        assert argv[0] == "/bin/x" and ("PROMPT" in argv or stdin == "PROMPT")
    assert [c for c, s in agent_connector.CLIS.items() if s["safe"]] == ["claude"]


# ---- telegram ----

def test_telegram_can_call_personal_agent():
    from test_community import make_bridge, outbox, update
    bridge = make_bridge()
    agent, _ = bridge.community.hub.create("kim", "claude")
    bridge.handle_update(update(7, f"@{agent.name} 안녕"))
    assert outbox(bridge) == [(7, f"{agent.name}: {OFFLINE}")]  # AI replies reach the sender's chat
