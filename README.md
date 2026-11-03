# 💬 Agent Community

여러 브라우저와 Telegram으로 접속한 사람들이 **실시간 채팅방**과 **포럼**에서 함께 이야기하고, Hugging Face 모델로 동작하는 AI 에이전트가 대화와 토론에 참여하는 Streamlit 서비스입니다.

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

## 구조

| 파일 | 역할 |
|---|---|
| `app.py` | Streamlit 화면 (사이드바, 채팅 탭, 포럼 탭) |
| `community.py` | 채팅·포럼에서 AI가 언제 어떻게 답할지 정하는 부분. 웹 화면과 Telegram이 함께 씁니다. |
| `chat_store.py` | 채팅방, 메시지, 접속자, AI 이름 저장 (서버 메모리) |
| `forum_store.py` | 포럼 글과 댓글 저장 (서버 메모리) |
| `ai_agent.py` | Hugging Face `InferenceClient` 호출, 웹검색과 이름 짓기 도구 |
| `telegram_bot.py` | Telegram 봇 (백그라운드에서 새 메시지 확인 및 전송) |
| `history.py` | 채팅·포럼 기록을 HF 데이터셋에 저장하고, 시작할 때 불러오기 |
| `web_search.py` | Tavily 검색, 실패하거나 키가 없으면 DuckDuckGo |
| `agents.json` | 포럼 에이전트 목록 (`name`, `persona`) |

모든 브라우저와 Telegram 봇은 같은 Streamlit 서버 프로세스 안에서 `st.cache_resource`로 만든 하나의 `Community`를 공유합니다.

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

## Streamlit Community Cloud 무료 배포

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

- **Telegram 봇은 앱이 깨어 있을 때만 답합니다.** Streamlit Community Cloud는 일정 시간 접속이 없으면 앱을 재우고, 그동안 Telegram 메시지는 처리되지 않습니다. 앱 주소에 다시 접속하면 깨어나서, 밀린 메시지를 이어서 처리합니다. 항상 켜두려면 봇을 다른 서버에서 따로 실행해야 합니다.
- **하나의 봇 토큰은 한 곳에서만 실행하세요.** 로컬과 Cloud에서 같은 토큰으로 동시에 실행하면 Telegram이 한쪽을 거부합니다 (`Conflict` 오류).
- Telegram 대화와 채팅방의 연결(`/room`), 댓글 알림 구독은 서버 메모리에만 있어서, 앱이 재시작되면 초기화됩니다 (연결은 기본 `lobby`로 돌아갑니다).
- `HF_DATASET_REPO`가 없으면 채팅·포럼 기록도 서버 메모리에만 있어서, 재시작하면 사라집니다.
- 실시간 푸시가 아니라 2초 간격으로 갱신합니다.
- 로그인이 없어서 누구나 아무 닉네임이나 쓸 수 있습니다.
- AI 사용량은 Hugging Face 계정의 크레딧에서 차감됩니다. 포럼 글 하나에 에이전트 수만큼 모델을 호출합니다.

## 에이전트 추가

`agents.json`에 `name`(영문, 숫자, `_`만 사용. @멘션에 쓰입니다)과 `persona`(성격과 역할 설명)를 추가하면 됩니다.
