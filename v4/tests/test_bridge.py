import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import agent_connector

ORIGIN = "https://p2p.example.hf.space"


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setattr(agent_connector, "find", lambda cli: "/fake/" + cli if cli in ("claude", "codex") else None)
    monkeypatch.setattr(agent_connector, "version", lambda path: "1.0")
    answers = iter([False])  # the user declines the first unsafe answer
    monkeypatch.setattr(agent_connector, "ask_yes", lambda q, default: next(answers))
    handler = agent_connector.make_bridge_handler({ORIGIN}, "code1234", False,
                                                  runner=lambda cli, path, prompt: f"{cli}:{prompt}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def call(base, path, body=None, origin=ORIGIN, code="code1234", method=None):
    headers = {"Origin": origin, "Authorization": f"Bearer {code}", "Content-Type": "application/json"}
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"{}")


def test_status_lists_clis_for_the_allowed_site(bridge):
    status, headers, body = call(bridge, "/status")
    assert status == 200 and headers["Access-Control-Allow-Origin"] == ORIGIN
    assert body["clis"]["claude"] == {"label": "Claude Code", "installed": True, "version": "1.0", "safe": True}
    assert body["clis"]["grok"]["installed"] is False


def test_other_sites_and_wrong_codes_are_refused(bridge):
    status, headers, _ = call(bridge, "/status", origin="https://evil.example")
    assert status == 403 and "Access-Control-Allow-Origin" not in headers
    assert call(bridge, "/status", code="nope")[0] == 401
    assert call(bridge, "/status", method="OPTIONS")[0] == 204
    assert call(bridge, "/status", origin="https://evil.example", method="OPTIONS")[0] == 403


def test_run_answers_and_unsafe_clis_need_approval(bridge):
    assert call(bridge, "/run", {"cli": "claude", "prompt": "안녕"})[2] == {"text": "claude:안녕"}
    assert call(bridge, "/run", {"cli": "codex", "prompt": "q"})[2] == {"declined": True}  # user said no
    assert call(bridge, "/run", {"cli": "grok", "prompt": "q"})[0] == 400  # not installed
    assert call(bridge, "/run", {"nope": 1})[0] == 400
