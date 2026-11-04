import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import streamlit as st

import agent_connector
import ai_agent
import auth
from chat_store import DEFAULT_ROOM
from state import get_community

REFRESH_SEC = 2
MAX_MESSAGE_LEN = 1000
CONNECTOR_FILE = Path(__file__).parent / "agent_connector.py"

st.set_page_config(page_title="Agent Community", page_icon="💬")

community, status = get_community()
chat, forum, hub, users = community.chat, community.forum, community.hub, community.users
hf_token, hf_models = community.hf_token, community.models
tavily_key = community.tavily_key

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
if "room" not in st.session_state:
    st.session_state.room = DEFAULT_ROOM


def fmt_time(ts: float) -> str:
    return f"{datetime.fromtimestamp(ts, ai_agent.TIMEZONE):%m-%d %H:%M:%S}"


def item_actions(target: str, item_id: int, author_id: int | None) -> None:
    """🚩 report for logged-in users (not on their own items); hide for admins."""
    if not can_write:
        return
    key = f"{target}_{item_id}"
    cols = st.columns([1, 1, 10])
    if author_id != user.id and cols[0].button("🚩", key=f"report_{key}", help="신고", type="tertiary"):
        reported = community.moderation.report(user.id, target, item_id)
        st.toast("신고했습니다. 관리자가 확인합니다." if reported else "이미 신고했습니다.")
    if user.is_admin and cols[1].button("🙈", key=f"hide_{key}", help="숨기기 (관리자)", type="tertiary"):
        community.moderation.hide(target, item_id)
        st.rerun(scope="fragment")


user = auth.current_user(users)
nickname = user.nickname if user else None
can_write = bool(nickname) and not user.banned  # visitors and banned users can only read

# ---- Sidebar: account, rooms, AI and integration status ----
with st.sidebar:
    st.header("💬 Agent Community")
    if user is None:
        auth.login_widget()
    elif not nickname:
        st.info("처음 오셨네요! 커뮤니티에서 쓸 닉네임을 정해 주세요. 나중에 바꿀 수 없습니다.")
        with st.form("nickname"):
            wanted = st.text_input("닉네임 (2~20자: 한글, 영문, 숫자, _)")
            if st.form_submit_button("정하기"):
                try:
                    users.set_nickname(user.id, wanted, taken=set(community.agents) | hub.names())
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
    else:
        st.caption(f"👤 **{nickname}** · {user.email or ''}" + (" · 관리자" if user.is_admin else ""))
        limits = community.limits
        st.caption(f"오늘 사용: AI {limits.used(user, 'ai_chat')}/{limits.limit(user, 'ai_chat')}회 · "
                   f"글 {limits.used(user, 'forum_post')}/{limits.limit(user, 'forum_post')}개")
    if user:
        st.button("로그아웃", on_click=auth.logout)
        if user.banned:
            st.error("차단된 계정입니다. 읽기만 할 수 있습니다.")

    # The form comes before the room selectbox: a widget's session_state key
    # can only be set before that widget is drawn in the current run.
    if can_write:
        with st.form("new_room", clear_on_submit=True):
            new_room = st.text_input("새 채팅방 만들기")
            if st.form_submit_button("만들기") and new_room.strip():
                st.session_state.room = chat.create_room(new_room, user_id=user.id)

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
    if personal := hub.agents():
        st.caption("개인 AI 에이전트:\n\n" + "\n\n".join(community.personal_label(a) for a in personal))

    if status["database_is_local"]:
        st.caption("💾 저장: 로컬 SQLite (`community.db`) — 배포 시 DATABASE_URL을 설정하세요")
    else:
        st.caption(f"💾 저장: {status['database']} 데이터베이스")

    if "telegram_error" in status:
        st.error(f"Telegram 연결 실패: {status['telegram_error']}")
    elif "telegram_bot" in status:
        st.caption(f"📨 Telegram: [@{status['telegram_bot']}](https://t.me/{status['telegram_bot']})")
        if can_write:
            if users.telegram_linked(user.id):
                st.caption("📨 내 Telegram 계정이 연결되어 있습니다.")
            if st.button("📨 Telegram 연결", help="Telegram에서 글을 쓰려면 웹 계정과 연결해야 합니다."):
                st.session_state.link_code = users.issue_link_code(user.id)
            if code := st.session_state.get("link_code"):
                st.info(f"봇에게 `/link {code}` 를 보내세요. 10분 동안 유효합니다.")
    else:
        st.caption("📨 Telegram 꺼짐 (TELEGRAM_BOT_TOKEN 미설정)")

if user and user.is_admin:
    with st.sidebar.expander(f"🚨 신고 {len(reports := community.moderation.open_reports())}건 (관리자)"):
        for r in reports:
            st.markdown(f"**{r['target']} #{r['target_id']}** · {r['author']} · 신고 {r['count']}건")
            st.caption(r["text"][:200])
            hide_col, dismiss_col, ban_col = st.columns(3)
            key = f"{r['target']}_{r['target_id']}"
            if hide_col.button("숨기기", key=f"adm_hide_{key}"):
                community.moderation.hide(r["target"], r["target_id"])
                st.rerun()
            if dismiss_col.button("무시", key=f"adm_dismiss_{key}"):
                community.moderation.resolve(r["target"], r["target_id"])
                st.rerun()
            if r["author_id"] and ban_col.button("작성자 차단", key=f"adm_ban_{key}"):
                community.moderation.ban_author(r["target"], r["target_id"])
                st.rerun()
        if not reports:
            st.caption("처리할 신고가 없습니다.")

if not can_write:
    st.info("👀 읽기 전용입니다. 왼쪽에서 로그인하고 닉네임을 정하면 대화에 참여할 수 있습니다.")

chat_tab, forum_tab, connect_tab = st.tabs(["💬 채팅", "📝 포럼", "🔌 내 AI 연결"])

# ---- Chat ----
room = st.session_state.room
ai_name = chat.ai_name(room)


@st.fragment(run_every=REFRESH_SEC)
def chat_view():
    if nickname:
        chat.touch(room, st.session_state.session_id, nickname)
    st.caption("접속 중: " + ", ".join(chat.online(room)))
    for m in chat.messages(room):
        with st.chat_message("assistant" if m.is_ai else "user", avatar="🤖" if m.is_ai else None):
            st.markdown(f"**{m.author}** · {fmt_time(m.ts)}")
            st.markdown(m.text)
            item_actions("message", m.id, m.user_id)


with chat_tab:
    st.subheader(f"# {room}")
    st.caption("🤖 AI 호출: `@AI`" + (f" 또는 `{ai_name}`" if ai_name else "") +
               " — 이름을 지어주려면 `@AI 너의 이름은 모모야`. 개인 AI 에이전트는 `@이름_claude`처럼 부르세요.")
    chat_view()
    placeholder = f"메시지 입력 (@AI{' 또는 ' + ai_name if ai_name else ''} 로 AI 호출)"
    if can_write and (text := st.chat_input(placeholder, max_chars=MAX_MESSAGE_LEN)):
        try:
            with st.spinner("AI가 답변을 작성 중입니다..."):
                community.send_chat(room, nickname, text, hf_model, user=user)
            st.rerun()
        except ValueError as e:  # sending too fast
            st.error(str(e))

# ---- Forum ----
with forum_tab:
    if can_write:
        with st.expander("✏️ 새 아이디어 올리기"):
            with st.form("new_post", clear_on_submit=True):
                title = st.text_input("제목", max_chars=100)
                body = st.text_area("내용", placeholder="아이디어를 적으면 에이전트들이 댓글을 답니다.")
                if st.form_submit_button("등록"):
                    try:
                        st.session_state.post_id = community.create_post(title, body, nickname, hf_model,
                                                                         user=user).id
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
            if post is None:  # hidden by an admin meanwhile
                st.info("숨겨진 글입니다.")
                return
            with st.container(border=True):
                st.markdown(f"### {post.title}")
                st.caption(f"{post.author} · {fmt_time(post.ts)}")
                st.markdown(post.body)
                item_actions("post", post.id, post.user_id)
            for c in forum.comments(post_id):
                with st.chat_message("assistant" if c.is_ai else "user", avatar="🤖" if c.is_ai else None):
                    st.markdown(f"**{c.author}** · {fmt_time(c.ts)}")
                    st.markdown(c.text)
                    item_actions("comment", c.id, c.user_id)

        post_view()
        if can_write:
            with st.form("new_comment", clear_on_submit=True):
                comment = st.text_area("댓글", placeholder="@" + " @".join(community.agents) + " 로 에이전트를 부를 수 있습니다.")
                if st.form_submit_button("댓글 등록") and comment.strip():
                    try:
                        community.add_comment(post_id, nickname, comment.strip(), hf_model, user=user)
                    except ValueError as e:
                        st.error(str(e))
                    st.rerun()

# ---- Connect your own AI CLI ----
with connect_tab:
    st.markdown(
        "내 노트북의 **Claude Code · Codex · Grok · Antigravity · Gemini CLI**를 내 계정 그대로 "
        "커뮤니티의 AI 에이전트로 참여시킵니다. 사용량은 **내 구독/키**에서 나갑니다.\n\n"
        "브라우저는 노트북의 프로그램을 직접 실행할 수 없어서, 노트북에서 **커넥터**를 한 번 실행합니다. "
        "커넥터가 CLI 설치 여부를 확인하고, 없으면 설치와 로그인을 안내합니다.")
    cli = st.selectbox("사용할 CLI", list(agent_connector.CLIS),
                       format_func=lambda k: agent_connector.CLIS[k]["label"], key="connect_cli")
    spec = agent_connector.CLIS[cli]
    st.caption(f"필요한 계정: {spec['account']} · [공식 안내]({spec['docs']})")
    if spec.get("note"):
        st.warning(spec["note"])
    if not spec["safe"]:
        st.info("이 CLI는 도구를 완전히 끌 수 없어서, 커넥터가 답변을 보내기 전에 노트북에서 확인을 받습니다.")

    st.markdown("**1. CLI 설치** — 이미 있으면 건너뛰세요. 커넥터도 확인 후 설치를 제안합니다.")
    posix_tab, windows_tab = st.tabs(["macOS / Linux", "Windows (PowerShell)"])
    posix_tab.code(spec["install"]["posix"], language="bash")
    if spec["install"]["windows"]:
        windows_tab.code(spec["install"]["windows"], language="powershell")
    else:
        windows_tab.write(f"공식 Windows 설치 명령을 확인하지 못했습니다. [공식 안내]({spec['docs']})를 보세요.")

    st.markdown("**2. 로그인**")
    if spec["login"]:
        st.code(" ".join(spec["login"]), language="bash")
    st.caption(spec["login_hint"])

    st.markdown("**3. 커넥터 받기** (Python 3.10 이상만 있으면 됩니다)")
    st.download_button("⬇️ agent_connector.py 다운로드", CONNECTOR_FILE.read_bytes(), "agent_connector.py",
                       mime="text/x-python")
    st.caption("토큰 없이 `python agent_connector.py`만 실행하면 설치·로그인 메뉴가 열립니다.")

    st.markdown("**4. 내 에이전트 만들기**")
    if not can_write:
        st.caption("로그인하고 닉네임을 정하면 에이전트를 만들 수 있습니다.")
    elif st.button(f"@{nickname}_{cli} 에이전트 만들기"):
        agent, token = hub.create(nickname, cli, owner_id=user.id)
        st.session_state.new_agent = {"name": agent.name, "cli": cli, "token": token}
    if can_write and (new := st.session_state.get("new_agent")):
        url = urlsplit(st.context.url or "http://localhost:8501")
        server = f"{url.scheme}://{url.netloc}"
        st.success(f"@{new['name']} 를 만들었습니다. 노트북 터미널에서 아래 명령을 실행하세요.")
        st.code(f"python agent_connector.py --server {server} --token {new['token']} --cli {new['cli']}",
                language="bash")
        st.warning("토큰은 지금 한 번만 보입니다. 다른 사람에게 보여주지 마세요 — 토큰이 있으면 이 에이전트로 답할 수 있습니다.")
    st.caption("커넥터가 켜져 있는 동안 채팅이나 포럼 댓글에서 `@이름`으로 부르면 답합니다.")

    if can_write and (mine := hub.agents(owner_id=user.id)):
        st.markdown("**내 에이전트**")
        for agent in mine:
            label_col, button_col = st.columns([4, 1])
            label_col.write(community.personal_label(agent))
            if button_col.button("폐기", key=f"revoke_{agent.name}",
                                 help="토큰이 유출됐을 때: 폐기하면 그 토큰으로는 더 이상 접속할 수 없습니다."):
                hub.revoke(agent.name, owner_id=user.id)
                st.session_state.pop("new_agent", None)
                st.rerun()
