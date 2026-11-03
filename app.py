import os
import uuid
from datetime import datetime

import streamlit as st

import ai_agent
from chat_store import DEFAULT_ROOM, ChatStore
from community import Community, load_agents
from forum_store import ForumStore
from history import HFHistory
from telegram_bot import TelegramBridge

REFRESH_SEC = 2
MAX_MESSAGE_LEN = 1000
MAX_NICKNAME = 20

st.set_page_config(page_title="Agent Community", page_icon="💬")


def get_setting(name: str, default: str | None = None) -> str | None:
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # no secrets.toml present
        pass
    return os.environ.get(name, default)


hf_token = get_setting("HF_TOKEN")
hf_models = list(get_setting("HF_MODELS") or ai_agent.DEFAULT_MODELS)
dataset_repo = get_setting("HF_DATASET_REPO")
tavily_key = get_setting("TAVILY_API_KEY")
telegram_token = get_setting("TELEGRAM_BOT_TOKEN")


@st.cache_resource
def get_community() -> tuple[Community, dict]:
    """One community per server process, shared by every browser session and the Telegram bot.

    Returns (community, status) — status holds history/telegram errors and the bot name.
    """
    listeners = []

    def emit(record: dict) -> None:
        for listener in listeners:
            listener(record)

    chat, forum = ChatStore(listener=emit), ForumStore(listener=emit)
    status = {}
    if hf_token and dataset_repo:
        try:
            history = HFHistory(dataset_repo, hf_token)
            records = history.load()
            chat.restore(records)
            forum.restore(records)
            listeners.append(history.append)
        except Exception as e:
            status["history_error"] = str(e)

    community = Community(chat, forum, load_agents(), hf_token, hf_models, tavily_key)
    if telegram_token:
        try:
            bridge = TelegramBridge(telegram_token, community)
            status["telegram_bot"] = bridge.call("getMe")["username"]  # also checks the token
            listeners.append(bridge.on_record)
            bridge.start()
        except Exception as e:
            status["telegram_error"] = str(e)
    return community, status


community, status = get_community()
chat, forum = community.chat, community.forum

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
if "room" not in st.session_state:
    st.session_state.room = DEFAULT_ROOM


def fmt_time(ts: float) -> str:
    return f"{datetime.fromtimestamp(ts, ai_agent.TIMEZONE):%m-%d %H:%M:%S}"


# ---- Sidebar: nickname, rooms, AI and integration status ----
with st.sidebar:
    st.header("💬 Agent Community")
    nickname = st.text_input("닉네임", max_chars=MAX_NICKNAME, key="nickname").strip()

    # The form comes before the room selectbox: a widget's session_state key
    # can only be set before that widget is drawn in the current run.
    with st.form("new_room", clear_on_submit=True):
        new_room = st.text_input("새 채팅방 만들기")
        if st.form_submit_button("만들기") and new_room.strip():
            st.session_state.room = chat.create_room(new_room)

    st.selectbox("채팅방", chat.rooms(), key="room")

    st.divider()
    hf_model = None
    if hf_token:
        st.success("AI 연결됨")
        hf_model = st.selectbox("AI 모델", hf_models, key="hf_model")
        st.caption("🔎 검색: " + ("Tavily (실패 시 DuckDuckGo)" if tavily_key else "DuckDuckGo"))
    else:
        st.warning("HF_TOKEN이 설정되지 않아 AI를 사용할 수 없습니다.")
    st.caption("포럼 에이전트: " + ", ".join(f"`@{n}`" for n in community.agents))

    if "history_error" in status:
        st.error(f"대화 기록 저장 실패: {status['history_error']}")
    elif dataset_repo and hf_token:
        st.caption(f"💾 기록 저장: `{dataset_repo}` (1분마다)")
    else:
        st.caption("💾 기록 저장 꺼짐 (HF_DATASET_REPO 미설정)")

    if "telegram_error" in status:
        st.error(f"Telegram 연결 실패: {status['telegram_error']}")
    elif "telegram_bot" in status:
        st.caption(f"📨 Telegram: [@{status['telegram_bot']}](https://t.me/{status['telegram_bot']})")
    else:
        st.caption("📨 Telegram 꺼짐 (TELEGRAM_BOT_TOKEN 미설정)")

if not nickname:
    st.info("왼쪽 사이드바에 닉네임을 입력하면 대화에 참여할 수 있습니다.")
    st.stop()

chat_tab, forum_tab = st.tabs(["💬 채팅", "📝 포럼"])

# ---- Chat ----
room = st.session_state.room
ai_name = chat.ai_name(room)


@st.fragment(run_every=REFRESH_SEC)
def chat_view():
    chat.touch(room, st.session_state.session_id, nickname)
    st.caption("접속 중: " + ", ".join(chat.online(room)))
    for m in chat.messages(room):
        with st.chat_message("assistant" if m.is_ai else "user", avatar="🤖" if m.is_ai else None):
            st.markdown(f"**{m.author}** · {fmt_time(m.ts)}")
            st.markdown(m.text)


with chat_tab:
    st.subheader(f"# {room}")
    st.caption("🤖 AI 호출: `@AI`" + (f" 또는 `{ai_name}`" if ai_name else "") +
               " — 이름을 지어주려면 `@AI 너의 이름은 모모야`")
    chat_view()
    placeholder = f"메시지 입력 (@AI{' 또는 ' + ai_name if ai_name else ''} 로 AI 호출)"
    if text := st.chat_input(placeholder, max_chars=MAX_MESSAGE_LEN):
        with st.spinner("AI가 답변을 작성 중입니다..."):
            community.send_chat(room, nickname, text, hf_model)
        st.rerun()

# ---- Forum ----
with forum_tab:
    with st.expander("✏️ 새 아이디어 올리기"):
        with st.form("new_post", clear_on_submit=True):
            title = st.text_input("제목", max_chars=100)
            body = st.text_area("내용", placeholder="아이디어를 적으면 에이전트들이 댓글을 답니다.")
            if st.form_submit_button("등록"):
                try:
                    st.session_state.post_id = community.create_post(title, body, nickname, hf_model).id
                except ValueError as e:
                    st.error(str(e))

    posts = forum.posts()
    if not posts:
        st.info("아직 글이 없습니다. 첫 아이디어를 올려보세요.")
    else:
        titles = {p.id: f"#{p.id} {p.title} — {p.author}" for p in posts}
        if st.session_state.get("post_id") not in titles:
            st.session_state.post_id = posts[0].id
        post_id = st.selectbox("글 선택", list(titles), format_func=titles.get, key="post_id")

        @st.fragment(run_every=REFRESH_SEC)
        def post_view():
            post = forum.post(post_id)
            with st.container(border=True):
                st.markdown(f"### {post.title}")
                st.caption(f"{post.author} · {fmt_time(post.ts)}")
                st.markdown(post.body)
            for c in forum.comments(post_id):
                with st.chat_message("assistant" if c.is_ai else "user", avatar="🤖" if c.is_ai else None):
                    st.markdown(f"**{c.author}** · {fmt_time(c.ts)}")
                    st.markdown(c.text)

        post_view()
        with st.form("new_comment", clear_on_submit=True):
            comment = st.text_area("댓글", placeholder="@" + " @".join(community.agents) + " 로 에이전트를 부를 수 있습니다.")
            if st.form_submit_button("댓글 등록") and comment.strip():
                community.add_comment(post_id, nickname, comment.strip(), hf_model)
                st.rerun()
