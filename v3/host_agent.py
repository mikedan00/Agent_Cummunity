"""The host's own Claude Code answering as a personal agent, right inside the app (no connector).

Only CLIs whose tools can all be turned off ("safe" in agent_connector.CLIS) run this
way, since other people's messages reach it; others connect with agent_connector.py,
which asks the owner before sending. The agent belongs to the host's account, so it
starts once the host has joined with a nickname.
"""

import shutil
import threading
import time

import agent_connector
from community import Community
from config import Config

IDLE_SEC = 5
WAIT_SEC = 10


class HostAgent:
    def __init__(self, community: Community, config: Config):
        self.community, self.config = community, config
        self.status = "꺼짐"

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="host-agent").start()

    def _agent(self, cli: str):
        host = next((u for u in self.community.users.admins() if u.nickname), None)
        if not host:
            return None
        hub = self.community.hub
        mine = [a for a in hub.agents(owner_id=host.id) if a.cli == cli]
        return mine[0] if mine else hub.create(host.nickname, cli, owner_id=host.id)[0]

    def step(self) -> bool:
        """Answer at most one task. False when there was nothing to do (or it can't run)."""
        cli = self.config["host_agent_cli"]
        if not cli:
            self.status = "꺼짐"
            return False
        spec = agent_connector.CLIS.get(cli)
        if not spec or not spec["safe"]:
            self.status = f"{cli}은(는) 앱 안에서 실행할 수 없습니다 (커넥터를 쓰세요)"
            return False
        path = shutil.which(spec["bin"])
        if not path:
            self.status = f"{spec['label']}이(가) 이 노트북에 없습니다"
            return False
        agent = self._agent(cli)
        if not agent:
            self.status = "호스트가 닉네임으로 입장하면 시작합니다"
            return False
        self.community.hub.touch(agent.name)
        self.status = f"🟢 @{agent.name} 동작 중"
        task = self.community.hub.next_task(agent.name, timeout=WAIT_SEC)
        if not task:
            return False
        answer = agent_connector.run_cli(cli, path, task["prompt"])
        self.community.finish_personal(agent, task["id"], answer)
        return True

    def _run(self) -> None:
        while True:
            try:
                # step() already waited for a task when the agent is running; otherwise pause.
                if not self.step() and not self.status.startswith("🟢"):
                    time.sleep(IDLE_SEC)
            except Exception as e:  # keep the app up whatever the CLI does
                self.status = f"오류: {e}"
                time.sleep(IDLE_SEC)
