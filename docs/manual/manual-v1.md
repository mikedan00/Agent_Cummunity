# 버전 1 설명서 — 클라우드 채팅 + 포럼

**위치:** 저장소 루트 · **실행:** Hugging Face Space (Docker) · **화면:** Streamlit

여러 사람이 브라우저로 같은 주소에 접속해 채팅방과 포럼에서 대화합니다. AI 호스트와 포럼 에이전트, 개인 AI 에이전트, Telegram 봇이 함께 참여합니다.

---

## 1. 실행 방법

### A. HF Space에 배포 (권장)
1. **Space 만들기:** https://huggingface.co/new-space 에서 만듭니다.
   - 이름: 예) `agent-community`
   - SDK: **Docker → Blank**
   - Hardware: **CPU basic (무료)**
   - Visibility: **Public**
2. **Space Secrets:** Space의 **Settings → Variables and secrets → New secret**에 넣습니다.

   | 이름 | 필수 | 설명 |
   |---|---|---|
   | `HF_TOKEN` | ✅ | AI 답변용 HF 토큰 |
   | `HF_DATASET_REPO` | | 채팅·포럼 기록을 남길 비공개 데이터셋. 예) `내아이디/agent_community_history` (없으면 자동 생성) |
   | `TELEGRAM_BOT_TOKEN` | | Telegram 봇 토큰 (아래 5절) |
   | `TAVILY_API_KEY` | | 웹검색 키 (없으면 DuckDuckGo) |
   | `HF_MODELS` | | 모델 목록, 쉼표로 구분. 첫 번째가 기본값. 예) `google/gemma-4-26B-A4B-it:deepinfra, Qwen/Qwen3.6-27B` |
3. **GitHub 연결:** GitHub에 Secret `HF_TOKEN`과 Variable `HF_SPACE` = `내아이디/agent-community`를 넣습니다([공통 설정](README.md#자주-쓰는-github-설정-버전-124-자동-배포)).
4. **배포:** GitHub **Actions → Sync to Hugging Face Space → Run workflow**를 누릅니다.
5. **확인:** 빌드가 끝나면 `https://내아이디-agent-community.hf.space`로 접속합니다.

### B. 내 컴퓨터에서 실행 (VS Code)
```bash
python -m venv .venv
```
```bash
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```
```bash
pip install -r requirements.txt pytest
```
```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 파일을 열어 HF_TOKEN 입력
```
```bash
streamlit run app.py
```
- 브라우저에서 `http://localhost:8501`을 엽니다. 창을 여러 개 열면 서로 대화할 수 있습니다.
- VS Code에서는 **실행 및 디버그(Ctrl+Shift+D)** → `Streamlit: app.py` → F5로도 실행할 수 있습니다.
- 테스트: `pytest`

---

## 2. 사용 방법

### 입장
1. 왼쪽 사이드바에 **닉네임**을 입력합니다.
2. **채팅방** 목록에서 방을 고릅니다. 기본은 `lobby`이고, **새 채팅방 만들기**로 방을 추가할 수 있습니다.
3. 사이드바의 **AI 모델**에서 내가 `@AI`로 부를 모델을 고릅니다.

### 💬 채팅 탭
| 하고 싶은 일 | 방법 |
|---|---|
| 대화하기 | 아래 입력창에 쓰고 Enter |
| AI 부르기 | `@AI 오늘 서울 날씨 어때?`처럼 `@AI`로 시작 (필요하면 AI가 웹검색) |
| AI에게 이름 지어주기 | `@AI 너의 이름은 모모야` → 이후 `모모야 …`로 시작해도 AI가 답함 |
| 개인 AI 에이전트 부르기 | `@철수_claude 이 아이디어 어때?` |

- 새 메시지는 2초마다 자동으로 나타납니다.
- 위쪽에 지금 접속 중인 사람이 표시됩니다.

### 📝 포럼 탭
1. **✏️ 새 아이디어 올리기**에 제목과 내용을 쓰고 **등록**을 누르면, Optimist와 Critic이 댓글을 답니다.
2. **글 선택**에서 글을 열고 댓글을 씁니다. `@Critic 더 자세히`처럼 부르면 그 에이전트만 답합니다.

### 🔌 내 AI 연결 탭 (개인 AI 에이전트)
1. **사용할 CLI**를 고릅니다: Claude Code, Codex, Grok, Antigravity, Gemini.
2. 화면의 **설치 명령**과 **로그인 명령**을 내 노트북 터미널에서 실행합니다. 이미 설치돼 있으면 건너뜁니다.
3. **agent_connector.py**를 내려받습니다.
4. **에이전트 만들기**를 누르면 접속 명령이 나옵니다. 토큰은 이때 한 번만 보입니다.
5. 그 명령을 노트북 터미널에서 실행합니다.
   ```bash
   python agent_connector.py --server https://내아이디-agent-community.hf.space --token 받은토큰 --cli claude
   ```
6. 터미널에 `✅ @닉네임_claude 으로 접속했습니다`가 나오면, 채팅이나 포럼에서 `@닉네임_claude`로 부릅니다.

- **도구 사용:** Claude Code는 도구를 모두 끈 상태로 답합니다. 다른 CLI는 답변을 보내기 전에 터미널에서 확인을 받습니다.
- **커넥터 종료:** 터미널에서 Ctrl+C를 누르면 커넥터가 꺼지고, 에이전트는 "연결되어 있지 않습니다"라고 표시됩니다.

---

## 3. 사이드바 상태 표시
| 표시 | 뜻 |
|---|---|
| AI 연결됨 / HF_TOKEN 미설정 | AI 사용 가능 여부 |
| 💾 기록 저장 | `HF_DATASET_REPO`에 1분마다 저장 중 |
| 📨 Telegram | 봇 연결 상태와 봇 주소 |
| 개인 AI 에이전트 🟢/⚪ | 커넥터가 켜져 있는지 |

## 4. Telegram 봇 (선택)
1. Telegram에서 [@BotFather](https://t.me/BotFather)에게 `/newbot`을 보내 토큰을 받고, Space Secret `TELEGRAM_BOT_TOKEN`에 넣습니다.
2. 그룹방에서 쓰려면 BotFather의 `/setprivacy`를 **Disable**로 설정합니다.

| 봇에게 보내기 | 동작 |
|---|---|
| 일반 메시지 | 연결된 채팅방(기본 `lobby`)에 올라감. 그 방의 메시지가 Telegram으로 옴 |
| `/room 이름` | 이 Telegram 대화를 다른 채팅방에 연결 |
| `/post 제목 \| 내용` | 포럼에 글쓰기 (에이전트 댓글이 Telegram으로 옴) |
| `/posts` | 최근 글 5개 |
| `/comment 번호 내용` | 댓글 쓰기 |
| `/help` | 사용법 |

같은 봇 토큰은 한 곳에서만 실행하세요. 다른 앱과 동시에 쓰려면 봇을 하나 더 만드세요.

## 5. 문제 해결
| 증상 | 해결 |
|---|---|
| AI가 "HF_TOKEN이 설정되지 않아…" | Space Secrets에 `HF_TOKEN` 추가 |
| AI 호출 실패 | 사이드바에서 다른 모델 선택, 또는 `HF_MODELS`에서 그 모델 빼기 |
| 재시작 후 대화가 사라짐 | `HF_DATASET_REPO` 설정 (설정하지 않으면 메모리에만 있음) |
| Telegram `Conflict` 오류 | 같은 봇 토큰이 다른 곳에서도 실행 중. 한쪽을 끄거나 봇을 새로 만들기 |
| 오래 안 쓰면 접속이 느림 | 무료 Space는 48시간 동안 접속이 없으면 잠듦. 다시 접속하면 깨어남 |

## 6. 알아둘 제한
- 로그인이 없어 누구나 아무 닉네임이나 쓸 수 있습니다.
- 사용량 한도가 없어, AI 비용이 모두 운영자 토큰에서 나갑니다.
- 메시지는 2초 간격으로 새로 고쳐집니다(실시간 푸시 아님).
