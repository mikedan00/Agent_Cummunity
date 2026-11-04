"""HTTP API for the local connector (agent_connector.py), mounted next to the Streamlit pages.

Every request carries the agent's token as 'Authorization: Bearer <token>'.
  POST /api/agent/hello           -> {"name", "owner", "cli"}
  GET  /api/agent/task?wait=25    -> {"task": {"id", "prompt", ...} | null}  (long poll)
  POST /api/agent/reply  {"task_id", "text"} -> {"ok": true}
"""

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from community import Community

MAX_WAIT_SEC = 25
MAX_REPLY_LEN = 4000


def _token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" else ""


def make_routes(get_community) -> list[Route]:
    """`get_community()` returns (community, status), like state.get_community."""

    def authed(handler):
        async def wrapper(request: Request):
            community: Community = get_community()[0]
            agent = community.hub.authenticate(_token(request))
            if not agent:
                return JSONResponse({"error": "invalid token"}, status_code=401)
            return await handler(request, community, agent)
        return wrapper

    @authed
    async def hello(request, community, agent):
        return JSONResponse({"name": agent.name, "owner": agent.owner, "cli": agent.cli})

    @authed
    async def task(request, community, agent):
        try:
            wait = min(float(request.query_params.get("wait", MAX_WAIT_SEC)), MAX_WAIT_SEC)
        except ValueError:
            wait = MAX_WAIT_SEC
        found = await asyncio.to_thread(community.hub.next_task, agent.name, wait)
        return JSONResponse({"task": found})

    @authed
    async def reply(request, community, agent):
        try:
            body = await request.json()
            task_id, text = str(body["task_id"]), str(body["text"])[:MAX_REPLY_LEN]
        except Exception:
            return JSONResponse({"error": "body must be {task_id, text}"}, status_code=400)
        if not community.finish_personal(agent, task_id, text):
            return JSONResponse({"error": "unknown task"}, status_code=404)
        return JSONResponse({"ok": True})

    return [
        Route("/api/agent/hello", hello, methods=["POST"]),
        Route("/api/agent/task", task, methods=["GET"]),
        Route("/api/agent/reply", reply, methods=["POST"]),
    ]
