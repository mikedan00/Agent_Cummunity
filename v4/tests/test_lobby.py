import pytest
from fastapi.testclient import TestClient

import lobby


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(lobby, "rooms", lobby.Rooms())
    return TestClient(lobby.app)


def test_newcomer_learns_peers_and_join_order(client):
    with client.websocket_connect("/ws/room1") as a:
        wa = a.receive_json()
        assert (wa["type"], wa["seq"], wa["peers"]) == ("welcome", 1, [])
        assert wa["iceServers"][0]["urls"] == [lobby.DEFAULT_STUN]
        with client.websocket_connect("/ws/room1") as b:
            wb = b.receive_json()
            assert wb["seq"] == 2 and wb["peers"] == [wa["id"]]
            with client.websocket_connect("/ws/other") as c:
                assert c.receive_json()["peers"] == []  # rooms are separate


def test_signals_are_forwarded_only_to_the_named_peer(client):
    with client.websocket_connect("/ws/room1") as a, client.websocket_connect("/ws/room1") as b:
        ida, idb = a.receive_json()["id"], b.receive_json()["id"]
        b.send_json({"type": "signal", "to": ida, "data": {"sdp": "offer"}})
        assert a.receive_json() == {"type": "signal", "from": idb, "data": {"sdp": "offer"}}
        b.send_json({"type": "signal", "to": "nobody", "data": {}})  # dropped
        a.send_json({"type": "signal", "to": idb, "data": {"sdp": "answer"}})
        assert b.receive_json()["data"] == {"sdp": "answer"}


def test_peer_left_is_announced_and_empty_rooms_vanish(client):
    with client.websocket_connect("/ws/room1") as a:
        a.receive_json()
        with client.websocket_connect("/ws/room1") as b:
            idb = b.receive_json()["id"]
        assert a.receive_json() == {"type": "peer-left", "id": idb}
    assert client.get("/api/health").json() == {"rooms": 0, "peers": 0}
    with client.websocket_connect("/ws/room1") as a:
        assert a.receive_json()["seq"] == 1  # a fresh room starts over


def test_bad_room_and_full_room(client, monkeypatch):
    with client.websocket_connect("/ws/BAD!") as ws:
        with pytest.raises(Exception):
            ws.receive_json()
    monkeypatch.setattr(lobby, "MAX_PEERS_PER_ROOM", 1)
    with client.websocket_connect("/ws/room1") as a:
        a.receive_json()
        with client.websocket_connect("/ws/room1") as b:
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
