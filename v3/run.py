#!/usr/bin/env python3
"""Agent Community host app — run it on your laptop and share the link.

  python run.py                     start (this window), open the admin page
  python run.py --background        start in the background and print the links
  python run.py --status            show the links of the running app
  python run.py --stop              stop the background app
  python run.py --install-autostart start automatically when you log in (Windows / Linux)
  python run.py --remove-autostart

Data (settings, database, cloudflared) lives in the data folder; see config.data_dir().
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from config import Config, data_dir  # noqa: E402

STATUS_FILE = "status.json"
LOG_FILE = "agent-community.log"
AUTOSTART_NAME = "agent-community"


def lan_ip() -> str:
    """This computer's address on the local network (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def write_status(folder: Path, **values) -> None:
    path = folder / STATUS_FILE
    status = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    status.update(values)
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def read_status(folder: Path) -> dict | None:
    path = folder / STATUS_FILE
    if not path.exists():
        return None
    status = json.loads(path.read_text(encoding="utf-8"))
    return status if pid_alive(status.get("pid")) else None


def pid_alive(pid) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def print_links(status: dict) -> None:
    print("\n=== Agent Community 실행 중 ===")
    invite = status.get("invite_url")
    public = invite and not invite.startswith(status.get("lan_url") or "-")
    print(f"초대 링크{' (인터넷)' if public else ''}: {invite or '터널 준비 중… 잠시 후 --status 로 확인하세요'}")
    if invite and not public:
        print("  ⚠️ 공개 터널이 꺼져 있거나 시작하지 못해, 같은 Wi-Fi에서만 열리는 주소입니다.")
    print(f"같은 Wi-Fi 주소:   {status.get('lan_url')}")
    print(f"관리 페이지 (이 노트북에서만): {status.get('admin_url')}")
    print("초대 링크에는 입장 비밀번호가 들어 있습니다. 함께할 사람에게만 보내세요.\n")


def serve(open_browser: bool) -> None:
    import uvicorn

    from host_agent import HostAgent
    from server import create_app
    from telegram_bot import TelegramBridge
    from tunnel import Tunnel, download_cloudflared, find_cloudflared

    folder = data_dir()
    config = Config.load(folder)
    port = int(config["port"])
    app = create_app(config)
    community = app.state.community
    app.state.lan_url = f"http://{lan_ip()}:{port}"
    admin_url = f"http://localhost:{port}/admin?key={config['admin_key']}"

    def invite(base: str) -> str:
        return base + (f"/?invite={config['password']}" if config["password"] else "/")

    write_status(folder, pid=os.getpid(), lan_url=app.state.lan_url, admin_url=admin_url, public_url=None,
                 invite_url=None, started=time.time())

    def on_url(url: str) -> None:
        app.state.public_url = url
        write_status(folder, public_url=url, invite_url=invite(url))
        print(f"🌐 공개 주소: {url}")

    app.state.on_settings = lambda: write_status(folder, invite_url=invite(app.state.public_url or app.state.lan_url))

    if config["tunnel"] == "off":
        write_status(folder, invite_url=invite(app.state.lan_url))  # same Wi-Fi only
    else:
        try:
            binary = find_cloudflared(folder / "bin") or download_cloudflared(folder / "bin")
            Tunnel(binary, port, on_url, token=config["tunnel_token"] if config["tunnel"] == "token" else "",
                   public_url=config["public_url"]).start()
        except Exception as e:
            print(f"⚠️  공개 터널을 시작하지 못했습니다: {e}\n   같은 Wi-Fi 주소로만 접속할 수 있습니다.")
            write_status(folder, invite_url=invite(app.state.lan_url))

    if config["telegram_token"]:
        try:
            bridge = TelegramBridge(config["telegram_token"], community)
            app.state.telegram_bot = bridge.call("getMe")["username"]
            app.state.listeners.append(bridge.on_record)
            bridge.start()
            if chat_id := config["admin_telegram_chat_id"]:
                app.state.on_global_cap = lambda cap: bridge.send(
                    int(chat_id), f"⚠️ 오늘 서비스 전체 AI 호출이 한도({cap}회)에 도달해 AI 답변이 멈췄습니다.")
        except Exception as e:
            print(f"⚠️  Telegram 연결 실패: {e}")

    host_agent = HostAgent(community, config)
    host_agent.start()
    app.state.host_agent_status = lambda: host_agent.status

    print_links({"lan_url": app.state.lan_url, "admin_url": admin_url})
    if open_browser:
        threading.Timer(1.5, webbrowser.open, args=(admin_url,)).start()
    # 0.0.0.0: reachable from the same Wi-Fi as well as through the tunnel.
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def start_background() -> None:
    folder = data_dir()
    if status := read_status(folder):
        print("이미 실행 중입니다.")
        print_links(status)
        return
    (folder / STATUS_FILE).unlink(missing_ok=True)
    log = open(folder / LOG_FILE, "a", encoding="utf-8")
    args = [sys.executable, str(HERE / "run.py"), "--no-browser"]
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        args[0] = str(pythonw) if pythonw.exists() else sys.executable
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(args, stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=flags)
    else:
        subprocess.Popen(args, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    print("백그라운드에서 시작했습니다. 공개 주소를 기다리는 중…")
    status = None
    for _ in range(60):
        time.sleep(1)
        status = read_status(folder)
        if status and status.get("invite_url"):
            break
    if not status:
        sys.exit(f"시작하지 못했습니다. 기록을 확인하세요: {folder / LOG_FILE}")
    print_links(status)
    webbrowser.open(status["admin_url"])


def stop() -> None:
    status = read_status(data_dir())
    if not status:
        print("실행 중이 아닙니다.")
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(status["pid"]), "/T", "/F"], capture_output=True)
    else:
        os.killpg(os.getpgid(status["pid"]), signal.SIGTERM)  # also stops cloudflared
    for _ in range(50):
        if not pid_alive(status["pid"]):
            break
        time.sleep(0.1)
    (data_dir() / STATUS_FILE).unlink(missing_ok=True)
    print("중지했습니다.")


def autostart_path() -> Path:
    if os.name == "nt":
        startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        return startup / f"{AUTOSTART_NAME}.cmd"
    return Path.home() / ".config" / "systemd" / "user" / f"{AUTOSTART_NAME}.service"


def install_autostart() -> None:
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        path.write_text(f'@echo off\r\n"{sys.executable}" "{HERE / "run.py"}" --background\r\n', encoding="utf-8")
        print(f"로그인할 때 자동으로 시작합니다: {path}")
        return
    path.write_text("\n".join([
        "[Unit]", "Description=Agent Community host app", "After=network-online.target", "",
        "[Service]", f"ExecStart={sys.executable} {HERE / 'run.py'} --no-browser", "Restart=on-failure", "",
        "[Install]", "WantedBy=default.target", ""]), encoding="utf-8")
    result = subprocess.run(["systemctl", "--user", "enable", "--now", AUTOSTART_NAME], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"systemd 사용자 서비스로 등록하고 시작했습니다: {path}\n상태: systemctl --user status {AUTOSTART_NAME}")
    else:
        print(f"서비스 파일을 만들었습니다: {path}\n다음 명령으로 켜세요: systemctl --user enable --now {AUTOSTART_NAME}")


def remove_autostart() -> None:
    path = autostart_path()
    if os.name != "nt":
        subprocess.run(["systemctl", "--user", "disable", "--now", AUTOSTART_NAME], capture_output=True)
    path.unlink(missing_ok=True)
    print("자동 시작을 해제했습니다.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Community 호스트 앱")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--background", action="store_true", help="백그라운드에서 시작")
    group.add_argument("--status", action="store_true", help="실행 중인 앱의 주소 보기")
    group.add_argument("--stop", action="store_true", help="백그라운드 앱 중지")
    group.add_argument("--install-autostart", action="store_true", help="로그인 시 자동 시작 등록")
    group.add_argument("--remove-autostart", action="store_true", help="자동 시작 해제")
    parser.add_argument("--no-browser", action="store_true", help="관리 페이지를 브라우저로 열지 않기")
    args = parser.parse_args()
    if args.background:
        start_background()
    elif args.status:
        status = read_status(data_dir())
        print_links(status) if status else print("실행 중이 아닙니다.")
    elif args.stop:
        stop()
    elif args.install_autostart:
        install_autostart()
    elif args.remove_autostart:
        remove_autostart()
    else:
        serve(open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
