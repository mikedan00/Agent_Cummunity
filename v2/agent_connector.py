#!/usr/bin/env python3
"""Agent Community local connector — run your own AI CLI as your agent in the community.

Usage:
  python agent_connector.py                 # setup menu: check, install and log in to CLIs
  python agent_connector.py --server URL --token TOKEN --cli claude

It needs only Python 3.10+ (no packages). The CLI runs on this computer with
your own account. Messages from other people reach it, so it runs in an empty
temporary folder in answer-only mode, and replies from CLIs whose tools can't
be fully turned off are shown to you for approval before they are sent.
"""

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

WINDOWS = os.name == "nt"
RUN_TIMEOUT_SEC = 300
POLL_WAIT_SEC = 25
HEARTBEAT_SEC = 20

# Install commands are the vendors' official ones. "safe" means every tool can be
# turned off, so replies are sent without asking.
CLIS = {
    "claude": {
        "label": "Claude Code",
        "bin": "claude",
        "install": {"posix": "curl -fsSL https://claude.ai/install.sh | bash",
                    "windows": "irm https://claude.ai/install.ps1 | iex"},
        "login": ["claude", "auth", "login"],
        "login_hint": "브라우저가 열리면 Claude 계정(Pro/Max 구독 또는 Console)으로 로그인하세요.",
        "account": "Claude Pro/Max 구독 또는 Anthropic Console 계정",
        "docs": "https://code.claude.com/docs/en/setup",
        "safe": True,
    },
    "codex": {
        "label": "Codex CLI (OpenAI)",
        "bin": "codex",
        "install": {"posix": "npm install -g @openai/codex", "windows": "npm install -g @openai/codex"},
        "login": ["codex", "login"],
        "login_hint": "브라우저가 열리면 ChatGPT 계정으로 로그인하세요. (Node.js 필요)",
        "account": "ChatGPT Plus/Pro 등 구독 또는 OpenAI API 키",
        "docs": "https://developers.openai.com/codex",
        "safe": False,
    },
    "grok": {
        "label": "Grok Build (xAI)",
        "bin": "grok",
        "install": {"posix": "curl -fsSL https://x.ai/cli/install.sh | bash", "windows": None},
        "login": ["grok", "login"],
        "login_hint": "브라우저가 열리면 X/xAI 계정으로 로그인하세요. 원격 PC라면 `grok login --device-auth`.",
        "account": "SuperGrok 또는 X Premium+ 구독",
        "docs": "https://docs.x.ai/build/cli/reference",
        "safe": False,
    },
    "agy": {
        "label": "Antigravity CLI (Google)",
        "bin": "agy",
        "install": {"posix": "curl -fsSL https://antigravity.google/cli/install.sh | bash",
                    "windows": "irm https://antigravity.google/cli/install.ps1 | iex"},
        "login": ["agy"],
        "login_hint": "처음 실행하면 나오는 안내대로 Google 계정에 로그인한 뒤 종료(/quit 또는 Ctrl+C)하세요.",
        "account": "개인 Google 계정 (Gemini CLI 개인 계정 지원 종료 후 후속 도구)",
        "docs": "https://codelabs.developers.google.com/genai-for-dev-antigravity-cli",
        "safe": False,
        "note": "백그라운드 실행 시 응답이 멈춘다는 버그 보고가 있습니다. 멈추면 시간 초과로 처리됩니다.",
    },
    "gemini": {
        "label": "Gemini CLI (API 키)",
        "bin": "gemini",
        "install": {"posix": "npm install -g @google/gemini-cli@latest",
                    "windows": "npm install -g @google/gemini-cli@latest"},
        "login": None,  # uses GEMINI_API_KEY
        "login_hint": "https://aistudio.google.com/apikey 에서 유료 Gemini API 키를 만들어 GEMINI_API_KEY로 설정하세요.",
        "account": "유료 Gemini API 키 (2026-06-18부터 개인 Google 계정 로그인은 지원 안 됨)",
        "docs": "https://geminicli.com/docs/get-started/installation/",
        "safe": False,
    },
}


# ---- local CLI checks ----

def _add_install_dirs_to_path() -> None:
    """Installers put binaries here; a fresh install isn't on PATH until a new shell."""
    home = os.path.expanduser("~")
    extra = [os.path.join(home, ".local", "bin"), os.path.join(home, ".grok", "bin")]
    if WINDOWS:
        extra.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Antigravity"))
    os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", "")] + extra)


def find(cli: str) -> str | None:
    return shutil.which(CLIS[cli]["bin"])


def version(path: str) -> str:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception:
        return "?"


def logged_in(cli: str, path: str) -> bool | None:
    """True/False when the CLI can tell us, None when it can't."""
    try:
        if cli == "claude":
            out = subprocess.run([path, "auth", "status"], capture_output=True, text=True, timeout=30)
            return bool(json.loads(out.stdout or "{}").get("loggedIn"))
        if cli == "codex":
            return subprocess.run([path, "login", "status"], capture_output=True, timeout=30).returncode == 0
        if cli == "gemini":
            return bool(os.environ.get("GEMINI_API_KEY"))
    except Exception:
        return None
    return None


def install(cli: str) -> bool:
    cmd = CLIS[cli]["install"]["windows" if WINDOWS else "posix"]
    if not cmd:
        print(f"이 OS용 설치 명령이 없습니다. 안내 페이지를 보세요: {CLIS[cli]['docs']}")
        return False
    print(f"\n실행할 설치 명령:\n  {cmd}\n(공식 설치 스크립트입니다. 출처: {CLIS[cli]['docs']})")
    if not ask_yes(f"{CLIS[cli]['label']}을(를) 설치할까요?", default=False):
        return False
    shell = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd] if WINDOWS else ["bash", "-c", cmd]
    subprocess.run(shell)
    _add_install_dirs_to_path()
    if find(cli):
        print(f"✅ 설치됨: {version(find(cli))}")
        return True
    print("설치 후에도 명령을 찾지 못했습니다. 새 터미널을 열고 다시 실행해 보세요.")
    return False


def login(cli: str) -> None:
    spec = CLIS[cli]
    print(f"\n🔐 {spec['label']} 로그인 — {spec['login_hint']}")
    if cli == "gemini":
        key = getpass.getpass("GEMINI_API_KEY (입력은 화면에 보이지 않으며, 이 실행 동안만 사용합니다): ").strip()
        if key:
            os.environ["GEMINI_API_KEY"] = key
        return
    subprocess.run([find(cli)] + spec["login"][1:])


def ensure_ready(cli: str) -> str:
    """Make sure the CLI is installed and logged in; returns its path or exits."""
    if not find(cli) and not install(cli):
        sys.exit(f"{CLIS[cli]['label']}이(가) 없어 종료합니다.")
    path = find(cli)
    status = logged_in(cli, path)
    if status is False or (status is None and ask_yes("로그인이 필요한가요? (처음 쓰는 경우 y)", default=False)):
        login(cli)
        if logged_in(cli, path) is False:
            sys.exit("로그인이 확인되지 않았습니다. 로그인 후 다시 실행하세요.")
    return path


def setup_menu() -> None:
    """Interactive menu: show which CLIs are installed/logged in, install or log in."""
    while True:
        print("\n=== Agent Community: 내 AI CLI 설치·로그인 메뉴 ===")
        keys = list(CLIS)
        for i, cli in enumerate(keys, 1):
            path = find(cli)
            state = "❌ 미설치"
            if path:
                login_state = {True: "로그인됨", False: "로그인 필요", None: "로그인 상태 알 수 없음"}[logged_in(cli, path)]
                state = f"✅ {version(path)} · {login_state}"
            print(f"  {i}. {CLIS[cli]['label']:<26} {state}")
            print(f"     계정: {CLIS[cli]['account']}")
        choice = input("\n번호를 고르면 설치/로그인합니다 (Enter = 종료): ").strip()
        if not choice:
            return
        if choice.isdigit() and 1 <= int(choice) <= len(keys):
            cli = keys[int(choice) - 1]
            if find(cli) or install(cli):
                login(cli)


# ---- answering ----

def build_command(cli: str, path: str, prompt: str, out_file: str) -> tuple[list[str], str | None]:
    """Answer-only invocation for each CLI: (argv, stdin text)."""
    if cli == "claude":
        # --tools "" turns off every built-in tool; --strict-mcp-config drops MCP servers.
        return [path, "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
                "--output-format", "text"], prompt
    if cli == "codex":
        return [path, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
                "--output-last-message", out_file, "-"], prompt
    if cli == "grok":
        return [path, "-p", prompt, "--no-auto-update", "--output-format", "plain"], None
    if cli == "agy":
        return [path, "-p", prompt], None
    if cli == "gemini":
        return [path, "-p", prompt, "--approval-mode", "default"], None
    raise ValueError(cli)


def run_cli(cli: str, path: str, prompt: str, timeout: float = RUN_TIMEOUT_SEC) -> str:
    """Run the CLI in an empty temporary folder and return its answer."""
    with tempfile.TemporaryDirectory(prefix="agent_community_") as work:
        out_file = os.path.join(work, "answer.txt")
        argv, stdin_text = build_command(cli, path, prompt, out_file)
        try:
            proc = subprocess.run(argv, input=stdin_text, stdin=None if stdin_text is not None else subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=timeout, cwd=work)
        except subprocess.TimeoutExpired:
            return f"(시간 초과: {int(timeout)}초 안에 답하지 못했습니다)"
        if os.path.exists(out_file):
            with open(out_file, encoding="utf-8") as f:
                return f.read().strip()
        if proc.returncode != 0:
            return f"(답변 생성 실패: {(proc.stderr or proc.stdout).strip()[:300]})"
        return proc.stdout.strip()


# ---- server ----

class Server:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 15) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                sys.exit("토큰이 올바르지 않습니다. 웹의 '🔌 내 AI 연결' 탭에서 에이전트를 다시 만드세요.")
            raise


def heartbeat(server: Server, stop: threading.Event) -> None:
    """Keep the agent shown as online while a long answer runs or you're approving one."""
    while not stop.wait(HEARTBEAT_SEC):
        try:
            server.call("POST", "/api/agent/hello", {})
        except Exception:
            pass


def ask_yes(question: str, default: bool) -> bool:
    answer = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not answer else answer.startswith("y")


def serve(server: Server, cli: str, path: str, auto_send: bool) -> None:
    me = server.call("POST", "/api/agent/hello", {})
    if me["cli"] != cli:
        sys.exit(f"이 토큰은 {me['cli']}용입니다. --cli {me['cli']} 로 실행하세요.")
    print(f"\n✅ @{me['name']} 으로 접속했습니다 ({CLIS[cli]['label']}). Ctrl+C 로 종료.")
    if not auto_send:
        print("ℹ️  이 CLI는 도구를 완전히 끌 수 없어, 답변을 보내기 전에 확인을 받습니다 (--yes 로 생략).")
    stop = threading.Event()
    threading.Thread(target=heartbeat, args=(server, stop), daemon=True).start()
    try:
        while True:
            try:
                task = server.call("GET", f"/api/agent/task?wait={POLL_WAIT_SEC}", timeout=POLL_WAIT_SEC + 15)["task"]
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                print(f"서버 연결 실패, 5초 후 재시도: {e}")
                time.sleep(5)
                continue
            if not task:
                continue
            where = f"채팅방 {task['room']}" if task["kind"] == "chat" else f"포럼 글 #{task['post_id']}"
            print(f"\n📩 {where}에서 호출됨 — 답변 작성 중...")
            answer = run_cli(cli, path, task["prompt"])
            print(f"--- 답변 ---\n{answer}\n-----------")
            if not auto_send and not ask_yes("이 답변을 보낼까요?", default=True):
                answer = "(주인이 답변을 보내지 않기로 했습니다)"
            server.call("POST", "/api/agent/reply", {"task_id": task["id"], "text": answer})
            print("📤 보냈습니다.")
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Community 로컬 커넥터")
    parser.add_argument("--server", help="커뮤니티 주소, 예: https://사용자-agent-community.hf.space")
    parser.add_argument("--token", help="웹의 '🔌 내 AI 연결' 탭에서 받은 토큰")
    parser.add_argument("--cli", choices=list(CLIS), help="사용할 CLI")
    parser.add_argument("--yes", action="store_true", help="확인 없이 답변 보내기 (권장하지 않음)")
    args = parser.parse_args()
    _add_install_dirs_to_path()

    if not (args.server and args.token and args.cli):
        setup_menu()
        return
    path = ensure_ready(args.cli)
    serve(Server(args.server, args.token), args.cli, path, auto_send=CLIS[args.cli]["safe"] or args.yes)


if __name__ == "__main__":
    main()
