"""Public https address for the host app through Cloudflare Tunnel (cloudflared).

- quick: `cloudflared tunnel --url http://127.0.0.1:PORT` gives a random https://*.trycloudflare.com
  address, no account needed. The address changes every time it starts. Cloudflare limits quick
  tunnels to 200 in-flight requests, does not support Server-Sent Events, and calls them
  testing-only.
- token: a named tunnel from a Cloudflare account (`tunnel run --token ...`); the address is
  the hostname set up in Cloudflare, given in config as public_url.
"""

import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

RELEASE_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/{name}"
# The tunnel's address, e.g. https://happy-cat-42.trycloudflare.com — not api.trycloudflare.com,
# which cloudflared also prints when it can't reach the tunnel service.
QUICK_URL_RE = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")
RESTART_DELAY_SEC = 5


def asset_name() -> str:
    """cloudflared's release file for this computer."""
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64",
            "i386": "386", "i686": "386", "x86": "386"}.get(machine, "amd64")
    if os.name == "nt":
        return f"cloudflared-windows-{arch}.exe"
    if platform.system() == "Darwin":
        return f"cloudflared-darwin-{arch}.tgz"
    return f"cloudflared-linux-{arch}"


def find_cloudflared(bin_dir: Path) -> str | None:
    local = bin_dir / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    return shutil.which("cloudflared") or (str(local) if local.exists() else None)


def download_cloudflared(bin_dir: Path, log: Callable[[str], None] = print) -> str:
    """Download the official cloudflared release from GitHub into bin_dir."""
    name = asset_name()
    if name.endswith(".tgz"):
        raise RuntimeError("macOS는 'brew install cloudflared'로 설치하세요.")
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    url = RELEASE_URL.format(name=name)
    log(f"cloudflared 내려받는 중: {url}")
    partial = target.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=120) as resp, open(partial, "wb") as out:
        shutil.copyfileobj(resp, out)
    partial.replace(target)
    target.chmod(0o755)
    return str(target)


class Tunnel:
    """Keeps cloudflared running in a thread and reports the public address via on_url."""

    def __init__(self, binary: str, port: int, on_url: Callable[[str], None], token: str = "",
                 public_url: str = "", log: Callable[[str], None] = print):
        self.binary, self.port, self.on_url, self.token = binary, port, on_url, token
        self.public_url, self.log = public_url, log
        self._proc: subprocess.Popen | None = None
        self._stopped = threading.Event()

    def command(self) -> list[str]:
        if self.token:
            return [self.binary, "tunnel", "--no-autoupdate", "run", "--token", self.token]
        return [self.binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"]

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="tunnel").start()

    def stop(self) -> None:
        self._stopped.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                self._proc = subprocess.Popen(self.command(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                              text=True, encoding="utf-8", errors="replace")
            except OSError as e:
                self.log(f"터널을 시작하지 못했습니다: {e}")
                return
            if self.token and self.public_url:
                self.on_url(self.public_url)
            for line in self._proc.stderr:
                if not self.token and (match := QUICK_URL_RE.search(line)):
                    self.on_url(match.group(0))
                if "ERR" in line or "failed" in line.lower():
                    self.log("cloudflared: " + line.strip())
            self._proc.wait()
            if not self._stopped.is_set():
                self.log(f"터널이 끊겼습니다. {RESTART_DELAY_SEC}초 후 다시 연결합니다 (주소가 바뀔 수 있습니다).")
                time.sleep(RESTART_DELAY_SEC)
