import pytest
from fastapi.testclient import TestClient

import lobby

KEY = "k" * 22


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(lobby, "rooms", lobby.Rooms())
    return TestClient(lobby.app)


def test_newcomer_learns_peers_and_join_order(client):
    with client.websocket_connect("/ws/room1?key=" + KEY) as a:
        wa = a.receive_json()
        assert (wa["type"], wa["seq"], wa["peers"]) == ("welcome", 1, [])
        assert wa["iceServers"][0]["urls"] == [lobby.DEFAULT_STUN]
        with client.websocket_connect("/ws/room1?key=" + KEY) as b:
            wb = b.receive_json()
            assert wb["seq"] == 2 and wb["peers"] == [wa["id"]]
            with client.websocket_connect("/ws/other?key=" + KEY) as c:
                assert c.receive_json()["peers"] == []  # rooms are separate


def test_signals_are_forwarded_only_to_the_named_peer(client):
    with client.websocket_connect("/ws/room1?key=" + KEY) as a, client.websocket_connect("/ws/room1?key=" + KEY) as b:
        ida, idb = a.receive_json()["id"], b.receive_json()["id"]
        b.send_json({"type": "signal", "to": ida, "data": {"sdp": "offer"}})
        assert a.receive_json() == {"type": "signal", "from": idb, "data": {"sdp": "offer"}}
        b.send_json({"type": "signal", "to": "nobody", "data": {}})  # dropped
        a.send_json({"type": "signal", "to": idb, "data": {"sdp": "answer"}})
        assert b.receive_json()["data"] == {"sdp": "answer"}


def test_peer_left_is_announced_and_empty_rooms_vanish(client):
    with client.websocket_connect("/ws/room1?key=" + KEY) as a:
        a.receive_json()
        with client.websocket_connect("/ws/room1?key=" + KEY) as b:
            idb = b.receive_json()["id"]
        assert a.receive_json() == {"type": "peer-left", "id": idb}
    assert client.get("/api/health").json() == {"rooms": 0, "peers": 0}
    with client.websocket_connect("/ws/room1?key=" + KEY) as a:
        assert a.receive_json()["seq"] == 1  # a fresh room starts over


def test_bad_room_and_full_room(client, monkeypatch):
    with client.websocket_connect("/ws/BAD!?key=" + KEY) as ws:
        with pytest.raises(Exception):
            ws.receive_json()
    monkeypatch.setattr(lobby, "MAX_PEERS_PER_ROOM", 1)
    with client.websocket_connect("/ws/room1?key=" + KEY) as a:
        a.receive_json()
        with client.websocket_connect("/ws/room1?key=" + KEY) as b:
            with pytest.raises(Exception):
                b.receive_json()


def test_turn_server_from_environment(monkeypatch):
    monkeypatch.setenv("TURN_URL", "turn:turn.example.com:3478,turns:turn.example.com:5349")
    monkeypatch.setenv("TURN_USERNAME", "u")
    monkeypatch.setenv("TURN_CREDENTIAL", "p")
    servers = lobby.ice_servers()
    assert servers[1] == {"urls": ["turn:turn.example.com:3478", "turns:turn.example.com:5349"],
                          "username": "u", "credential": "p"}


def test_new_room_code(client):
    code = client.get("/api/new-room").json()["room"]
    assert lobby.ROOM_RE.match(code)


def test_ticket_is_a_valid_signature_over_room_id_seq_and_key(client):
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    with client.websocket_connect("/ws/room1?key=" + KEY) as a:
        w = a.receive_json()
    raw = base64.urlsafe_b64decode(w["ticket"] + "==")
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub = lobby.SIGNING_KEY.public_key()
    pub.verify(der, f"room1|{w['id']}|{w['seq']}|{KEY}".encode(), ec.ECDSA(hashes.SHA256()))
    assert w["lobbyKey"]["crv"] == "P-256"
    try:
        pub.verify(der, f"room1|{w['id']}|1|someone-else-key".encode(), ec.ECDSA(hashes.SHA256()))
        raise AssertionError("ticket must be bound to the key")
    except Exception as e:
        assert type(e).__name__ == "InvalidSignature"


def test_missing_key_is_rejected(client):
    with client.websocket_connect("/ws/room1") as ws:
        with pytest.raises(Exception):
            ws.receive_json()


def test_ai_uses_host_token_without_operator(client, monkeypatch):
    seen = []
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(lobby, "chat_completion", lambda token, model, messages, max_tokens: seen.append((token, model)) or "답")
    assert client.get("/api/ai/status").json()["operator"] is False
    r = client.post("/api/ai", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 400 and "호스트의 HF 토큰" in r.json()["error"]
    r = client.post("/api/ai", json={"messages": [{"role": "user", "content": "hi"}], "token": "hf_host", "model": "evil/model"})
    assert r.json() == {"text": "답", "model": lobby.DEFAULT_MODELS[0]}  # unknown models fall back to the list
    assert seen == [("hf_host", lobby.DEFAULT_MODELS[0])]


def test_operator_token_is_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_operator")
    monkeypatch.setattr(lobby, "OPERATOR_AI_PER_HOUR", 2)
    monkeypatch.setattr(lobby, "quota", lobby.OperatorQuota())
    monkeypatch.setattr(lobby, "chat_completion", lambda token, *a: token)
    body = {"messages": [{"role": "user", "content": "hi"}]}
    assert [client.post("/api/ai", json=body).status_code for _ in range(3)] == [200, 200, 429]
    assert client.post("/api/ai", json={**body, "token": "hf_own"}).status_code == 200  # own token: no quota


def test_ai_errors_do_not_leak_details(client, monkeypatch):
    def boom(*a):
        raise RuntimeError("401 for token hf_secret")
    monkeypatch.setattr(lobby, "chat_completion", boom)
    r = client.post("/api/ai", json={"messages": [{"role": "user", "content": "x"}], "token": "hf_secret"})
    assert r.status_code == 502 and "hf_secret" not in r.text
