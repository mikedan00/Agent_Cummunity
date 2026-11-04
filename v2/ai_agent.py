"""Hugging Face AI agent: answers chat messages starting with @AI and writes forum comments.

The agent offers the model a web_search tool; the model decides when it
needs current information. Models whose provider rejects tools still
answer, just without search.
"""

import json
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from huggingface_hub import InferenceClient

from chat_store import Message

# Models users can pick in the sidebar. "model:provider" pins an Inference
# Provider; without a suffix Hugging Face picks one automatically.
# Override the whole list with HF_MODELS in Streamlit Secrets.
DEFAULT_MODELS = [
    "google/gemma-4-26B-A4B-it:deepinfra",
    "Qwen/Qwen3.6-27B",
    "Qwen/Qwen2.5-7B-Instruct",
]
AI_NAME = "AI"
MENTION = "@ai"
HISTORY_LIMIT = 20
MAX_TOOL_ROUNDS = 3
TIMEZONE = ZoneInfo("Asia/Seoul")
SYSTEM_PROMPT = (
    "You are the AI host of a group chat room. Several people talk here at once; "
    "each user message is prefixed with the speaker's nickname. "
    "Answer the person who called you, briefly and helpfully, in the language they used. "
    "Your name in this room: {name}. People call you with @AI or by that name. "
    "If someone gives you a new name (e.g. 'your name is Momo'), call the set_ai_name tool "
    "with just the name, without honorifics or particles. "
    "Current date and time: {now} (Asia/Seoul). "
    "If the question needs current or real-time information (news, prices, weather, "
    "recent events, anything after your training data), call the web_search tool and "
    "cite the URLs you used."
)
FORUM_PROMPT = (
    "{persona}\n\n"
    "You are taking part in a discussion forum where humans and AI agents debate ideas. "
    "Reply with a single forum comment in plain text, in the same language as the post. "
    "Keep it under 200 words. Current date and time: {now} (Asia/Seoul). "
    "If you need current facts, call the web_search tool and cite the URLs you used."
)
TOOLS = [{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current or real-time information.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "set_ai_name",
        "description": "Change the name the AI is called by in this chat room, when a user gives it a name.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The new name only, e.g. 모모"}},
            "required": ["name"],
        },
    },
}]


def is_ai_mention(text: str, ai_name: str | None = None) -> bool:
    """True if the message starts with @AI or with the AI's room name (e.g. '모모야 ...')."""
    text = text.strip().lower()
    return text.startswith(MENTION) or bool(ai_name) and text.startswith(ai_name.lower())


def build_messages(history: list[Message], limit: int = HISTORY_LIMIT, now: datetime | None = None,
                   ai_name: str | None = None) -> list[dict]:
    """Turn recent room history into chat-completion messages."""
    now = now or datetime.now(TIMEZONE)
    prompt = SYSTEM_PROMPT.format(now=f"{now:%Y-%m-%d %H:%M}", name=ai_name or AI_NAME)
    out = [{"role": "system", "content": prompt}]
    for m in history[-limit:]:
        if m.is_ai:
            out.append({"role": "assistant", "content": m.text})
        else:
            out.append({"role": "user", "content": f"{m.author}: {m.text}"})
    return out


def split_provider(model: str) -> tuple[str, str | None]:
    """'org/model:provider' -> ('org/model', 'provider'); no suffix -> provider None (auto)."""
    name, _, provider = model.partition(":")
    return name, provider or None


def ask(token: str, model: str, history: list[Message], search: Callable[[str], str],
        set_name: Callable[[str], str] | None = None, ai_name: str | None = None,
        max_tokens: int = 1024, client=None) -> str:
    """Answer the latest message.

    `search(query)` returns formatted search results; `set_name(name)` renames the AI
    in this room and returns the saved name.
    """
    client = client or _client(token, model)
    return _complete(client, build_messages(history, ai_name=ai_name), TOOLS, search, set_name, max_tokens)


def forum_reply(token: str, model: str, persona: str, thread: str, search: Callable[[str], str],
                max_tokens: int = 1024, client=None) -> str:
    """Write one forum comment as the agent described by `persona`, given the whole thread as text."""
    client = client or _client(token, model)
    now = datetime.now(TIMEZONE)
    messages = [
        {"role": "system", "content": FORUM_PROMPT.format(persona=persona, now=f"{now:%Y-%m-%d %H:%M}")},
        {"role": "user", "content": f"Here is the thread so far:\n\n{thread}\n\nWrite your comment."},
    ]
    return _complete(client, messages, TOOLS[:1], search, None, max_tokens)  # web_search only


def _client(token: str, model: str) -> InferenceClient:
    name, provider = split_provider(model)
    return InferenceClient(model=name, provider=provider, token=token, timeout=90)


def _complete(client, messages: list[dict], tools: list[dict], search: Callable[[str], str],
              set_name: Callable[[str], str] | None, max_tokens: int) -> str:
    """Run the chat completion, executing tool calls for up to MAX_TOOL_ROUNDS rounds."""
    try:
        resp = client.chat_completion(messages, tools=tools, tool_choice="auto", max_tokens=max_tokens)
    except Exception:
        # The provider may not support tools: answer without search.
        resp = client.chat_completion(messages, max_tokens=max_tokens)
        return (resp.choices[0].message.content or "").strip()

    queries = []
    for _ in range(MAX_TOOL_ROUNDS):
        msg = resp.choices[0].message
        if not msg.tool_calls:
            break
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.function.name, "arguments": _as_json(c.function.arguments)}}
                           for c in msg.tool_calls],
        })
        for c in msg.tool_calls:
            args = _parse_args(c.function.arguments)
            try:
                if c.function.name == "set_ai_name" and set_name:
                    result = f"Name changed to {set_name(args.get('name', ''))}."
                else:
                    queries.append(args.get("query", ""))
                    result = search(args.get("query", ""))
            except Exception as e:
                result = f"Tool failed: {e}"
            messages.append({"role": "tool", "tool_call_id": c.id, "content": result})
        resp = client.chat_completion(messages, tools=tools, tool_choice="auto", max_tokens=max_tokens)

    answer = (resp.choices[0].message.content or "").strip()
    if queries:
        answer += "\n\n🔎 검색: " + ", ".join(f"`{q}`" for q in queries)
    return answer


def _parse_args(arguments) -> dict:
    if isinstance(arguments, dict):
        return arguments
    try:
        return json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {}


def _as_json(arguments) -> str:
    return arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
