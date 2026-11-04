import os
import stat
import sys
import time

import pytest

import agent_connector
import db
import run
import tunnel
from config import Config
from host_agent import HostAgent
from server import create_app

pytestmark = pytest.mark.skipif(os.name == "nt", reason="uses shebang scripts")


def script(path, body):
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_tunnel_reports_quick_url_and_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnel, "RESTART_DELAY_SEC", 0.1)
    fake = script(tmp_path / "cloudflared", "import sys\n"
                  "assert sys.argv[1:4] == ['tunnel', '--no-autoupdate', '--url'], sys.argv\n"
                  "sys.stderr.write('failed to request quick Tunnel: Post \"https://api.trycloudflare.com/tunnel\"\\n')\n"
                  "sys.stderr.write('INF Your quick Tunnel has been created! Visit it at:\\n')\n"
                  "sys.stderr.write('INF |  https://happy-cat-42.trycloudflare.com  |\\n')\n")
    urls = []
    logged = []
    t = tunnel.Tunnel(fake, 8765, urls.append, log=logged.append)
    t.start()
    deadline = time.time() + 5
    while len(urls) < 2 and time.time() < deadline:  # it exits, so it restarts
        time.sleep(0.05)
    t.stop()
    assert urls[:2] == ["https://happy-cat-42.trycloudflare.com"] * 2  # never the api address
    assert any("failed to request quick Tunnel" in m for m in logged)


def test_named_tunnel_uses_token_and_configured_url(tmp_path):
    t = tunnel.Tunnel("/bin/cloudflared", 1, lambda u: None, token="TOKEN", public_url="https://chat.example.com")
    assert t.command() == ["/bin/cloudflared", "tunnel", "--no-autoupdate", "run", "--token", "TOKEN"]


def test_find_cloudflared_in_data_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: None)
    assert tunnel.find_cloudflared(tmp_path) is None
    (tmp_path / "cloudflared").write_text("x")
    assert tunnel.find_cloudflared(tmp_path) == str(tmp_path / "cloudflared")
    assert tunnel.asset_name().startswith("cloudflared-")


def make_host(tmp_path, cli="claude"):
    config = Config(tmp_path / "config.json")
    config.update({"host_agent_cli": cli, "password": ""})
    app = create_app(config, engine=db.make_engine("sqlite://"))
    return app, config, HostAgent(app.state.community, config)


def test_host_agent_waits_for_the_host_then_answers(tmp_path, monkeypatch):
    monkeypatch.setattr("host_agent.WAIT_SEC", 0.2)
    fake = script(tmp_path / "claude", "import sys\nsys.stdin.read()\nprint('호스트 Claude 답변')")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    app, config, host_agent = make_host(tmp_path)
    community = app.state.community
    assert not host_agent.step() and "입장하면" in host_agent.status

    host = community.users.set_nickname(community.users.login("local", "h", None).id, "호스트")
    community.users.set_role(host.id, "admin")
    guest = community.users.set_nickname(community.users.login("local", "g", None).id, "손님")
    host_agent.step()  # creates @호스트_claude and marks it online
    assert host_agent.status == "🟢 @호스트_claude 동작 중" and community.hub.is_online("호스트_claude")

    community.send_chat("lobby", "손님", "@호스트_claude 안녕하세요", user=guest)
    assert host_agent.step()
    assert community.chat.messages("lobby")[-1].text == "호스트 Claude 답변"
    assert fake  # the fake CLI was the one found on PATH


def test_host_agent_refuses_unsafe_or_missing_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    _, _, host_agent = make_host(tmp_path, cli="codex")
    assert not host_agent.step() and "커넥터" in host_agent.status
    (tmp_path / "second").mkdir()
    _, _, host_agent = make_host(tmp_path / "second", cli="claude")  # no claude on PATH
    assert not host_agent.step() and "없습니다" in host_agent.status
    assert agent_connector.CLIS["claude"]["safe"]


def test_status_file_and_lan_ip(tmp_path):
    run.write_status(tmp_path, pid=os.getpid(), lan_url="http://1.2.3.4:8765")
    run.write_status(tmp_path, public_url="https://x.trycloudflare.com")
    status = run.read_status(tmp_path)
    assert status["lan_url"] == "http://1.2.3.4:8765" and status["public_url"].endswith("trycloudflare.com")
    run.write_status(tmp_path, pid=999999999)
    assert run.read_status(tmp_path) is None  # that process is not running
    assert run.lan_ip().count(".") == 3


def test_linux_autostart_writes_a_user_service(tmp_path, monkeypatch):
    monkeypatch.setattr(run.Path, "home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(run.subprocess, "run", lambda args, **kw: calls.append(args) or type("R", (), {"returncode": 1})())
    run.install_autostart()
    unit = (tmp_path / ".config/systemd/user/agent-community.service").read_text()
    assert "ExecStart=" in unit and "run.py --no-browser" in unit and "WantedBy=default.target" in unit
    assert calls[0][:3] == ["systemctl", "--user", "enable"]
    run.remove_autostart()
    assert not (tmp_path / ".config/systemd/user/agent-community.service").exists()
