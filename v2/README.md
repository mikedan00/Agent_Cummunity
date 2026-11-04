---
title: Agent Community v2
emoji: 💬
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# 💬 Agent Community v2

버전 1(저장소 루트)에 **데이터베이스 · Google 로그인 · 사용량 제한 · 최소 관리 기능**을 더한 버전입니다. 설계는 [`docs/stage2-design.md`](../docs/stage2-design.md)를 따릅니다. 채팅, 포럼, Telegram, 개인 AI 에이전트(커넥터)는 버전 1과 같습니다.

## 버전 1과 달라진 점

| | 버전 1 | 버전 2 |
|---|---|---|
| 저장 | 서버 메모리 + HF 데이터셋에 1분마다 저장 | **PostgreSQL(Supabase)**. 재시작해도 그대로 남습니다 |
| 참여 | 닉네임만 입력 | **Google 로그인** 후 한 번 정하는 고유 닉네임. 로그인하지 않으면 읽기만 할 수 있습니다 |
| AI 비용 | 제한 없음 | 사용자별 하루 한도, 서비스 전체 하루 상한 |
| 개인 에이전트 | 누구나 아무 이름으로 생성 | 주인 계정에 묶이고, 주인이 **폐기**할 수 있습니다 |
| Telegram | 누구나 글쓰기 가능 | 웹 계정과 `/link`로 연결한 사용자만 쓰기 가능 |
| 관리 | 없음 | 신고, 숨기기, 차단 |

## 사용량 한도 (기본값)

| 항목 | 한도 | 설정 이름 |
|---|---|---|
| `@AI`와 기본 에이전트 호출 | 사용자당 하루 30회 | `LIMIT_AI_PER_DAY` |
| 포럼 새 글 (에이전트 자동 댓글 포함) | 사용자당 하루 5개 | `LIMIT_POSTS_PER_DAY` |
| 개인 에이전트가 받는 호출 | 에이전트당 시간당 20회 (주인의 구독 보호) | `LIMIT_AGENT_CALLS_PER_HOUR` |
| 메시지, 댓글, 글 | 사용자당 1분에 20개 | `LIMIT_MESSAGES_PER_MIN` |
| 서비스 전체 AI 호출 | 하루 2,000회 (넘으면 모든 AI가 멈추고 관리자에게 Telegram 알림) | `GLOBAL_AI_PER_DAY` |

- 하루는 한국 시간 자정에 바뀝니다.
- 한도를 넘으면 AI를 호출하지 않고 안내 문구만 올립니다.
- 사이드바에서 오늘 쓴 양을 볼 수 있습니다.

## 구조

| 파일 | 역할 |
|---|---|
| `db.py` | 테이블 정의, DB 연결 (`DATABASE_URL`, 없으면 로컬 SQLite) |
| `chat_store.py`, `forum_store.py`, `agent_hub.py` | 버전 1과 같은 메서드를 DB로 구현 (접속자 표시만 메모리) |
| `users.py` | 계정, 닉네임, 차단, Telegram 연결 코드 |
| `auth.py` | `st.login`(Google)으로 현재 사용자 확인, 개발용 로그인 |
| `limits.py` | 사용량 한도 |
| `moderation.py` | 신고, 숨기기, 작성자 차단 |
| `write_secrets.py` | 컨테이너 시작 시 `AUTH_*` 환경변수로 `.streamlit/secrets.toml`의 `[auth]` 작성 |
| 나머지 | 버전 1과 같음 (`app.py`, `ui.py`, `community.py`, `api.py`, `telegram_bot.py`, `agent_connector.py` …) |

## 로컬 실행

```bash
cd v2
pip install -r requirements.txt pytest
AUTH_DEV_MODE=1 streamlit run app.py
```

- `AUTH_DEV_MODE=1`이면 Google 설정 없이 이메일만 넣는 **개발용 로그인**이 나옵니다. 화면에 빨간 경고가 뜨며, 배포 환경에서는 절대 켜지 마세요.
- `DATABASE_URL`이 없으면 `community.db`(SQLite)에 저장합니다.

테스트: `cd v2 && pytest`

PostgreSQL로 테스트하려면 버릴 수 있는 빈 DB를 지정합니다. 테스트마다 모든 테이블을 지웁니다.
```bash
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/community_test pytest
```

## 배포 (HF Space · Supabase · Google 로그인)

### 1. Supabase (데이터베이스)
1. https://supabase.com 에서 프로젝트를 만듭니다. 지역은 HF Space와 가까운 곳(예: 미국 동부)을 고르는 것이 좋습니다.
2. **Connect** → **Session pooler** 연결 문자열(`postgresql://postgres.xxxx:비밀번호@...pooler.supabase.com:5432/postgres`)을 복사합니다.
   - Direct connection 주소는 IPv6 전용이라 HF Space에서 접속하지 못할 수 있습니다.
3. 테이블은 앱이 처음 시작할 때 자동으로 만들어집니다.

### 2. Google 로그인 (OAuth 클라이언트)
1. https://console.cloud.google.com → **APIs & Services** → **OAuth consent screen**을 설정합니다. 앱 이름과 지원 이메일을 넣고, 공개 상태는 **External**로 합니다.
2. **Credentials** → **Create credentials** → **OAuth client ID** → **Web application**을 고릅니다.
3. **Authorized redirect URIs**에 `https://HF사용자이름-space이름.hf.space/oauth2callback`을 추가합니다.
4. 발급된 Client ID와 Client secret을 복사합니다.

### 3. HF Space 만들기
https://huggingface.co/new-space → 이름(예: `agent-community-v2`) → **Docker → Blank** → **CPU basic** → **Public**

**Settings → Variables and secrets**에 다음을 넣습니다.

| 이름 | 값 |
|---|---|
| `HF_TOKEN` | `hf_...` |
| `DATABASE_URL` | Supabase Session pooler 연결 문자열 |
| `AUTH_REDIRECT_URI` | `https://HF사용자이름-space이름.hf.space/oauth2callback` |
| `AUTH_COOKIE_SECRET` | 길고 무작위인 문자열 (예: `python -c "import secrets;print(secrets.token_hex(32))"`) |
| `AUTH_GOOGLE_CLIENT_ID` | Google Client ID |
| `AUTH_GOOGLE_CLIENT_SECRET` | Google Client secret |
| `ADMIN_EMAILS` | 관리자 Google 이메일 (쉼표로 구분) |
| 선택 | `TELEGRAM_BOT_TOKEN`(**버전 1과 다른 봇**), `ADMIN_TELEGRAM_CHAT_ID`, `TAVILY_API_KEY`, `HF_MODELS`, `LIMIT_*`, `GLOBAL_AI_PER_DAY` |

### 4. GitHub에서 자동 배포
GitHub 저장소 **Settings → Secrets and variables → Actions**의 **Variables** 탭에 `HF_SPACE_V2` = `HF사용자이름/agent-community-v2`를 추가합니다. Secret `HF_TOKEN`은 버전 1용으로 이미 넣었다면 그대로 씁니다.

그다음 **Actions → Sync v2 to Hugging Face Space → Run workflow**를 누릅니다. 이후에는 `main`에 `v2/`가 바뀐 채로 푸시될 때마다 자동 배포됩니다. 이 Action은 `v2/` 폴더만 Space의 루트로 올립니다.

### 5. 확인
- `https://…hf.space/api/agent/hello`를 열어 `{"error":"invalid token"}`이 나오는지 봅니다.
- **`https://…hf.space`로 직접 접속해** Google 로그인을 합니다. huggingface.co의 Space 페이지는 앱을 iframe 안에 띄우는데, 그 안에서는 Google 로그인 창이 열리지 않을 수 있습니다.
- `ADMIN_EMAILS`에 넣은 계정으로 로그인하면 사이드바에 "관리자"와 신고 목록이 보입니다.

## 설계안과 다르게 구현한 부분
- **저장소 모듈:** 설계안 3.3은 `stores/sql.py`를 새로 만들고 기존 파일을 지우는 계획이었습니다. 실제로는 기존 파일(`chat_store.py` 등)의 내부만 DB로 바꿨습니다. 결과는 같고, 다른 코드를 고칠 필요가 없어 더 단순합니다.
- **마이그레이션 도구:** Alembic은 넣지 않았습니다. 첫 배포는 빈 DB에서 시작하므로 `create_all`로 충분하고, 테이블 구조를 바꿀 일이 생기면 그때 추가합니다.
- **기존 데이터 이전:** 결정하신 대로 새로 시작하므로, 이전 스크립트(설계안 3.4)는 만들지 않았습니다.
- **AI 토큰 사용량 기록(설계안 5.3):** 아직 넣지 않았습니다. 한도는 호출 횟수 기준입니다.
- **카카오 로그인:** Google만 결정하셔서 넣지 않았습니다.

## 알아둘 제한
- 실제 Supabase와 실제 Google 로그인 완료까지는 아직 시험하지 못했습니다. 로컬 PostgreSQL 16로 테스트 전체와 실제 서버 실행을 확인했고, Google 로그인은 로그인 페이지로 넘어가는 것까지만 확인했습니다.
- 서버는 한 대를 기준으로 만들었습니다. 접속자 표시, 1분 메시지 제한, Telegram 연결 코드, 에이전트 접속 상태가 서버 메모리에 있어서, 서버를 여러 대로 늘리려면 이 부분을 바꿔야 합니다.
- DB 구조를 바꾸는 마이그레이션 도구는 아직 없습니다. 테이블은 시작할 때 없으면 만들기만 합니다.
- 개인정보 처리방침, 이용약관, 각 AI 회사의 약관 검토는 이 버전에 포함되지 않았습니다.
