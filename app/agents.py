import json
import os
import re
from pathlib import Path

from app import db

MODEL = "claude-opus-5-5"
AGENTS_FILE = Path(__file__).resolve().parent.parent / "agents.json"

FORUM_RULES = (
    "You are taking part in a discussion forum where humans and AI agents debate ideas. "
    "Reply with a single forum comment in plain text, in the same language as the post. "
    "Keep it under 200 words."
)


def load_agents() -> dict[str, dict]:
    agents = json.loads(AGENTS_FILE.read_text(encoding="utf-8"))
    return {a["name"]: a for a in agents}


def mentioned_agents(text: str, agents: dict[str, dict]) -> list[str]:
    names = re.findall(r"@(\w+)", text)
    return [n for n in dict.fromkeys(names) if n in agents]


def build_thread(post: dict, comments: list[dict]) -> str:
    lines = [f"[Post by {post['author']}] {post['title']}\n{post['body']}"]
    for c in comments:
        lines.append(f"[Comment by {c['author']}] {c['body']}")
    return "\n\n".join(lines)


def generate_reply(agent: dict, thread: str) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return f"(demo mode: set ANTHROPIC_API_KEY for a real reply from {agent['name']})"

    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=f"{agent['persona']}\n\n{FORUM_RULES}",
        messages=[{"role": "user", "content": f"Here is the thread so far:\n\n{thread}\n\nWrite your comment."}],
    )
    if response.stop_reason == "refusal":
        return "(this agent declined to reply)"
    return "".join(b.text for b in response.content if b.type == "text").strip()


def reply(post_id: int, agent_names: list[str]) -> None:
    """Have each named agent read the thread and post a comment. Runs as a background task."""
    agents = load_agents()
    for name in agent_names:
        post = db.get_post(post_id)
        thread = build_thread(post, db.list_comments(post_id))
        try:
            text = generate_reply(agents[name], thread)
        except Exception as e:  # keep the forum working if one API call fails
            text = f"(error generating reply: {type(e).__name__})"
        db.add_comment(post_id, text, name, is_agent=True)
