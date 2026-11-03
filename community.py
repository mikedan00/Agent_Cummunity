"""What happens when someone talks — shared by the Streamlit app and the Telegram bot.

- Chat: a message starting with @AI (or the AI's room name) gets an answer from the AI host.
- Forum: a new post gets a comment from every agent in agents.json; a comment that
  @mentions an agent gets a reply from that agent. Agent comments never call agents.
"""

import json
import re
import threading
from pathlib import Path

import ai_agent
import web_search
from chat_store import ChatStore
from forum_store import ForumStore

AGENTS_FILE = Path(__file__).parent / "agents.json"
NO_TOKEN = "HF_TOKEN이 설정되지 않아 답할 수 없습니다."


def load_agents(path: Path = AGENTS_FILE) -> dict[str, dict]:
    return {a["name"]: a for a in json.loads(path.read_text(encoding="utf-8"))}


def mentioned_agents(text: str, agents: dict[str, dict]) -> list[str]:
    names = re.findall(r"@(\w+)", text)
    return [n for n in dict.fromkeys(names) if n in agents]


def model_label(model: str) -> str:
    return ai_agent.split_provider(model)[0].split("/")[-1]


class Community:
    def __init__(self, chat: ChatStore, forum: ForumStore, agents: dict[str, dict],
                 hf_token: str | None, models: list[str], tavily_key: str | None = None,
                 background: bool = True):
        self.chat, self.forum, self.agents = chat, forum, agents
        self.hf_token, self.models, self.tavily_key = hf_token, models, tavily_key
        self.background = background  # run forum agent replies in a thread (False in tests)

    def _search(self, query: str) -> str:
        return web_search.format_results(web_search.search(query, self.tavily_key))

    # ---- chat ----

    def is_ai_call(self, room: str, text: str) -> bool:
        return ai_agent.is_ai_mention(text, self.chat.ai_name(room))

    def send_chat(self, room: str, author: str, text: str, model: str | None = None) -> None:
        self.chat.add_message(room, author, text)
        if self.is_ai_call(room, text):
            self.answer_chat(room, model)

    def answer_chat(self, room: str, model: str | None = None) -> None:
        model = model or self.models[0]
        if not self.hf_token:
            reply = NO_TOKEN
        else:
            try:
                reply = ai_agent.ask(self.hf_token, model, self.chat.messages(room), self._search,
                                     set_name=lambda name: self.chat.set_ai_name(room, name),
                                     ai_name=self.chat.ai_name(room))
            except Exception as e:
                reply = f"AI 호출 실패: {e}"
        # ai_name again: the AI may have just been renamed in this reply.
        author = f"{self.chat.ai_name(room) or ai_agent.AI_NAME} · {model_label(model)}"
        self.chat.add_message(room, author, reply or "(빈 응답)", is_ai=True)

    # ---- forum ----

    def create_post(self, title: str, body: str, author: str, model: str | None = None):
        post = self.forum.create_post(title, body, author)
        self._run(self.agents_reply, post.id, list(self.agents), model)
        return post

    def add_comment(self, post_id: int, author: str, text: str, model: str | None = None) -> None:
        self.forum.add_comment(post_id, author, text)
        self.reply_to_mentions(post_id, text, model)

    def reply_to_mentions(self, post_id: int, text: str, model: str | None = None) -> None:
        if names := mentioned_agents(text, self.agents):
            self._run(self.agents_reply, post_id, names, model)

    def agents_reply(self, post_id: int, names: list[str], model: str | None = None) -> None:
        """Each named agent reads the whole thread, then comments."""
        model = model or self.models[0]
        for name in names:
            if not self.hf_token:
                text = NO_TOKEN
            else:
                try:
                    text = ai_agent.forum_reply(self.hf_token, model, self.agents[name]["persona"],
                                                self.thread_text(post_id), self._search)
                except Exception as e:
                    text = f"AI 호출 실패: {e}"
            self.forum.add_comment(post_id, name, text or "(빈 응답)", is_ai=True)

    def thread_text(self, post_id: int) -> str:
        post = self.forum.post(post_id)
        lines = [f"[Post by {post.author}] {post.title}\n{post.body}"]
        lines += [f"[Comment by {c.author}] {c.text}" for c in self.forum.comments(post_id)]
        return "\n\n".join(lines)

    def _run(self, fn, *args) -> None:
        if self.background:
            threading.Thread(target=fn, args=args, daemon=True).start()
        else:
            fn(*args)
