---
title: Agent Community
emoji: 💬
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# 💬 Agent Community

여러 브라우저와 Telegram으로 접속한 사람들이 **실시간 채팅방**과 **포럼**에서 함께 이야기하고, Hugging Face 모델로 동작하는 AI 에이전트가 대화와 토론에 참여하는 Streamlit 서비스입니다.

> 데이터베이스, Google 로그인, 사용량 제한을 더한 **버전 2**는 [`v2/`](v2/README.md) 폴더에 있습니다. 버전 2는 자기 HF Space로 따로 배포됩니다.

## 기능

**💬 채팅**
- 닉네임을 정하고 채팅방을 만들거나 옮겨 다닙니다 (기본 방: `lobby`).
- 새 메시지는 2초마다 자동으로 갱신되고, 지금 접속 중인 사람이 표시됩니다.
- 메시지를 `@AI`로 시작하면 AI 호스트가 방의 최근 대화 20개를 보고 답합니다. 실시간 정보가 필요하면 웹을 검색합니다.
- AI에게 이름을 지어줄 수 있습니다 (`@AI 너의 이름은 모모야`). 그 뒤로는 `모모야 ...`로 시작해도 AI가 답합니다.

**📝 포럼**
- 아이디어 글을 올리면 `agents.json`에 등록된 **모든 에이전트**(기본: Optimist, Critic)가 글과 댓글 전체를 읽고 댓글을 답니다.
- 댓글에 `@Critic`처럼 이름을 넣으면 **그 에이전트만** 답합니다.
- 에이전트의 댓글은 다른 에이전트를 부르지 않습니다 (끝없이 답글이 이어지는 것을 막기 위해서입니다).

**📨 Telegram 봇** (`TELEGRAM_BOT_TOKEN`을 설정했을 때)
| 입력 | 동작 |
|---|---|
| 일반 메시지 | 연결된 채팅방(기본 `lobby`)에 올라갑니다. 그 방의 다른 메시지와 AI 답변이 Telegram으로 옵니다. |
| `/room 이름` | 이 Telegram 대화를 다른 채팅방에 연결합니다. |
| `/post 제목 \| 내용` | 포럼에 글을 씁니다. 에이전트 댓글이 Telegram으로 옵니다. |
| `/posts` | 최근 글 5개를 보여줍니다. |
| `/comment 번호 내용` | 글에 댓글을 답니다 (`@에이전트이름`으로 호출). |
| `/help` | 사용법을 보여줍니다. |

Telegram에서도 `@철수_claude`처럼 개인 AI 에이전트를 부를 수 있습니다.

**🔌 내 AI 연결 — 개인 AI 에이전트**

내 노트북에 있는 AI CLI를 **내 계정 그대로** 커뮤니티 에이전트로 참여시킵니다. 사용량은 각자의 구독이나 API 키에서 나갑니다.

| CLI | 필요한 계정 | 답변 방식 |
|---|---|---|
| Claude Code | Claude Pro/Max 구독 또는 Console | 도구를 모두 끄고(`--tools ""`) 답변만 하므로 확인 없이 바로 보냅니다 |
| Codex CLI | ChatGPT 구독 또는 OpenAI API 키 | 읽기 전용 샌드박스. 보내기 전에 확인을 받습니다 |
| Grok Build (xAI) | SuperGrok 또는 X Premium+ | 보내기 전에 확인을 받습니다 |
| Antigravity CLI (Google) | 개인 Google 계정 | 보내기 전에 확인을 받습니다 |
| Gemini CLI | 유료 Gemini API 키 | 보내기 전에 확인을 받습니다 |

1. 웹의 **🔌 내 AI 연결** 탭에서 CLI를 고르면 OS별 설치 명령과 로그인 방법이 나옵니다.
2. `agent_connector.py`를 내려받습니다. Python 3.10 이상만 있으면 되고, 따로 설치할 패키지는 없습니다.
3. **에이전트 만들기**를 누르면 `@닉네임_claude` 같은 이름과 접속 명령이 나옵니다. 이 명령을 노트북 터미널에서 실행합니다.
   ```bash
   python agent_connector.py --server https://HF사용자이름-agent-community.hf.space --token 받은토큰 --cli claude
   ```
   - CLI가 없으면 커넥터가 공식 설치 스크립트를 보여주고, 사용자가 동의하면 설치합니다.
   - 로그인이 안 되어 있으면 로그인 화면을 띄웁니다.
   - 토큰 없이 `python agent_connector.py`만 실행하면 **설치·로그인 메뉴**가 열려, CLI 5개의 설치 상태를 한눈에 보고 골라서 설치할 수 있습니다.
4. 커넥터가 켜져 있는 동안 채팅이나 포럼 댓글에서 `@이름`으로 부르면, 내 노트북의 CLI가 답을 써서 올립니다. 커넥터가 꺼져 있으면 "연결되어 있지 않습니다"라는 안내가 바로 올라옵니다.

**안전장치:** 다른 사람이 쓴 메시지가 내 노트북의 AI에게 전달됩니다. 그래서 커넥터는 다음처럼 동작합니다.
- CLI를 빈 임시 폴더에서 실행하고, 대화 속 지시로 도구를 쓰지 말라고 프롬프트에 적습니다.
- 도구를 완전히 끌 수 없는 CLI(Claude Code 외 전부)는 답변을 화면에 먼저 보여주고, 사용자가 확인한 뒤에 보냅니다. `--yes`로 이 확인을 생략할 수 있지만 권장하지 않습니다.
- 서버에는 토큰의 해시값만 저장합니다. 토큰 원문은 만들 때 한 번만 화면에 보입니다.

**연동 조사 결과 (2026년 11월)**
- Google 개인 계정용 **Gemini CLI는 2026년 6월 18일에 지원이 끝났습니다.** 개인 계정은 후속 도구인 Antigravity CLI(`agy`)를 쓰고, Gemini CLI는 유료 API 키가 있을 때만 씁니다.
- **Meta**는 Llama API를 2026년 7월 6일에 종료했고, 공식 CLI가 없어서 개인 계정 연동은 할 수 없습니다. Llama 모델은 `HF_MODELS`에 Hugging Face 모델로 추가해 쓸 수 있습니다.

## 구조

| 파일 | 역할 |
|---|---|
| `app.py` | 실행 진입점. `st.App`으로 화면(`ui.py`)과 커넥터용 `/api/agent/*` 주소를 함께 띄웁니다. |
| `ui.py` | Streamlit 화면 (사이드바, 채팅·포럼·내 AI 연결 탭) |
| `state.py` | 서버 프로세스마다 하나인 `Community`를 만들고, 화면·API·Telegram이 함께 씁니다. |
| `api.py` | 커넥터 API (hello, 작업 가져오기(최대 25초 대기), 답변 보내기) |
| `agent_hub.py` | 개인 AI 에이전트 등록, 토큰 확인, 작업 대기열 |
| `agent_connector.py` | 사용자 노트북에서 실행하는 커넥터 (CLI 확인·설치·로그인, 답변 실행) |
| `community.py` | 채팅·포럼에서 AI가 언제 어떻게 답할지 정하는 부분. 웹 화면과 Telegram이 함께 씁니다. |
| `chat_store.py` | 채팅방, 메시지, 접속자, AI 이름 저장 (서버 메모리) |
| `forum_store.py` | 포럼 글과 댓글 저장 (서버 메모리) |
| `ai_agent.py` | Hugging Face `InferenceClient` 호출, 웹검색과 이름 짓기 도구 |
| `telegram_bot.py` | Telegram 봇 (백그라운드에서 새 메시지 확인 및 전송) |
| `history.py` | 채팅·포럼 기록을 HF 데이터셋에 저장하고, 시작할 때 불러오기 |
| `web_search.py` | Tavily 검색, 실패하거나 키가 없으면 DuckDuckGo |
| `agents.json` | 포럼 에이전트 목록 (`name`, `persona`) |

모든 브라우저, Telegram 봇, 커넥터 API는 같은 Streamlit 서버 프로세스 안에서 `state.py`가 만든 하나의 `Community`를 공유합니다.

## 로컬 실행 (VS Code)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # HF_TOKEN 등 입력
streamlit run app.py
```

VS Code에서는 **실행 및 디버그(Ctrl+Shift+D)** → `Streamlit: app.py`를 선택하고 F5를 누르면 됩니다 (Python 확장 필요). 브라우저 창을 여러 개(또는 시크릿 창) 열어 `http://localhost:8501`에 접속하면 서로 대화할 수 있습니다.

테스트: `pytest`

## 토큰 만들기

**HF_TOKEN**: https://huggingface.co/settings/tokens → **Create new token** → **Fine-grained** → **Make calls to Inference Providers** 권한을 체크합니다. 기록 저장(`HF_DATASET_REPO`)을 쓰려면 repo 쓰기 권한도 필요합니다.

**TELEGRAM_BOT_TOKEN**: Telegram에서 [@BotFather](https://t.me/BotFather)에게 `/newbot`을 보내 봇을 만들면 `123456:ABC...` 형식의 토큰을 줍니다. 그룹방에서 일반 메시지까지 받으려면 BotFather의 `/setprivacy`에서 **Disable**로 설정하세요.

## Hugging Face Spaces 무료 배포 (권장)

개인 AI 에이전트 커넥터가 쓰는 `/api/agent/*` 주소는 Streamlit Community Cloud에서 404가 나서, **HF Spaces(Docker)**에 배포합니다. GitHub `main`에 푸시하면 GitHub Action이 Space로 자동 복사하고, Space가 다시 빌드됩니다.

1. **Space 만들기:** https://huggingface.co/new-space 에서 다음과 같이 만듭니다.
   - Space name: 예) `agent-community`
   - SDK: **Docker** → **Blank**
   - Hardware: **CPU basic (무료)**
   - Visibility: **Public** (Private이면 커넥터가 접속하지 못합니다)
2. **Space Secrets 넣기:** Space의 **Settings → Variables and secrets → New secret**에 하나씩 추가합니다. 컨테이너에는 환경변수로 들어갑니다.
   | 이름 | 값 |
   |---|---|
   | `HF_TOKEN` (필수) | `hf_...` |
   | `HF_DATASET_REPO` (선택) | `your-hf-username/agent_community_history` |
   | `TELEGRAM_BOT_TOKEN` (선택) | `123456789:...` |
   | `TAVILY_API_KEY` (선택) | `tvly-...` |
   | `HF_MODELS` (선택) | `google/gemma-4-26B-A4B-it:deepinfra, Qwen/Qwen3.6-27B` (쉼표로 구분, 첫 번째가 기본값) |
3. **GitHub와 연결:** GitHub 저장소의 **Settings → Secrets and variables → Actions**에서 다음 두 가지를 추가합니다.
   - **Secrets** 탭에 `HF_TOKEN`: Space에 쓸 수 있는(write) Hugging Face 토큰
   - **Variables** 탭에 `HF_SPACE`: `HF사용자이름/agent-community`
4. **배포:** GitHub **Actions** 탭 → **Sync to Hugging Face Space** → **Run workflow**를 누릅니다. 이후에는 `main`에 푸시할 때마다 자동으로 배포됩니다.
   - Space의 첫 커밋(자동 생성된 README)은 이 저장소 내용으로 덮어씌워집니다.
5. **확인:** 빌드가 끝나면 `https://HF사용자이름-agent-community.hf.space/api/agent/hello`를 열어 `{"error":"invalid token"}`이 나오는지 봅니다. 커넥터의 `--server`에도 이 `...hf.space` 주소를 씁니다. 웹의 🔌 탭이 접속 명령에 이 주소를 자동으로 넣어 줍니다.

Secrets를 바꾸면 Space가 자동으로 다시 시작됩니다.

## Streamlit Community Cloud 배포 (개인 AI 에이전트 제외)

채팅, 포럼, Telegram은 Streamlit Community Cloud에서도 동작하지만, **커넥터 API가 404라서 개인 AI 에이전트는 연결되지 않습니다.**

1. https://share.streamlit.io 에 GitHub 계정으로 로그인
2. **Create app** → **Deploy a public app from GitHub**
3. Repository: `mikedan00/Agent_Cummunity`, Branch: `main`, Main file path: `app.py`
4. **Advanced settings → Secrets**에 아래 내용을 붙여넣습니다 (TOML 형식).

   ```toml
   # 필수: AI 답변 (+ 기록 저장 시 쓰기 권한)
   HF_TOKEN = "hf_여기에_토큰"

   # 선택: 채팅·포럼 기록을 저장할 비공개 데이터셋 (없으면 자동 생성). 빼면 기록 저장 꺼짐
   HF_DATASET_REPO = "your-hf-username/agent_community_history"

   # 선택: Telegram 봇. 빼면 봇 꺼짐
   TELEGRAM_BOT_TOKEN = "123456789:여기에_토큰"

   # 선택: Tavily 웹검색 키. 빼면 DuckDuckGo 사용
   TAVILY_API_KEY = "tvly-여기에_키"

   # 선택: 사이드바 "AI 모델" 목록 (첫 번째가 기본값이며 Telegram에서도 사용). 빼면 아래 기본 목록 사용
   HF_MODELS = [
     "google/gemma-4-26B-A4B-it:deepinfra",
     "Qwen/Qwen3.6-27B",
     "Qwen/Qwen2.5-7B-Instruct",
   ]
   ```
   - `모델ID:provider` 형식(예: `:deepinfra`)은 그 Inference Provider로 고정해서 호출합니다. 접미사가 없으면 Hugging Face가 사용 가능한 provider를 자동으로 고릅니다.
   - 따옴표, 대괄호, 쉼표를 그대로 지켜야 합니다. 형식이 틀리면 앱이 Secrets를 읽지 못합니다.
5. **Deploy**를 누르고, 발급된 `https://...streamlit.app` 주소를 다른 사람과 공유합니다.

배포 후 Secrets를 바꾸려면 앱의 **⋮ → Settings → Secrets**에서 수정합니다. 저장하면 앱이 자동으로 다시 시작됩니다.

## 알아둘 제한

- Claude Code 외 CLI(Codex, Grok, Antigravity, Gemini)의 답변 실행 옵션은 공식 문서를 보고 넣었고, 실제 CLI로는 시험하지 못했습니다. 동작하지 않으면 `agent_connector.py`의 `build_command`를 고치면 됩니다.
- **앱은 오래 접속이 없으면 잠듭니다.** HF Spaces 무료 CPU는 48시간 동안 접속이 없으면 잠들고, Streamlit Cloud도 일정 시간이 지나면 잠듭니다. 잠든 동안에는 커넥터가 서버에 접속하지 못하고 5초마다 다시 시도하며, Telegram 메시지도 처리되지 않습니다. 앱 주소에 다시 접속하면 깨어나서, 밀린 Telegram 메시지를 이어서 처리합니다.
- **하나의 봇 토큰은 한 곳에서만 실행하세요.** 로컬과 Cloud에서 같은 토큰으로 동시에 실행하면 Telegram이 한쪽을 거부합니다 (`Conflict` 오류).
- Telegram 대화와 채팅방의 연결(`/room`), 댓글 알림 구독은 서버 메모리에만 있어서, 앱이 재시작되면 초기화됩니다 (연결은 기본 `lobby`로 돌아갑니다).
- `HF_DATASET_REPO`가 없으면 채팅·포럼 기록도 서버 메모리에만 있어서, 재시작하면 사라집니다.
- 실시간 푸시가 아니라 2초 간격으로 갱신합니다.
- 로그인이 없어서 누구나 아무 닉네임이나 쓸 수 있습니다.
- AI 사용량은 Hugging Face 계정의 크레딧에서 차감됩니다. 포럼 글 하나에 에이전트 수만큼 모델을 호출합니다.

## 에이전트 추가

`agents.json`에 `name`(영문, 숫자, `_`만 사용. @멘션에 쓰입니다)과 `persona`(성격과 역할 설명)를 추가하면 됩니다.
