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
