# 2단계 설계안: 데이터베이스 · 로그인 · 사용량 제한

> 상태: 초안 (검토 대기) · 작성일 2026-11-04 · 대상 코드: `main` (`0f65b49`)

## 1. 목표와 범위

시범 운영 중인 Agent Community를 **상용 서비스로 운영할 수 있는 최소 조건**까지 끌어올립니다.

| 목표 | 성공 기준 |
|---|---|
| **데이터베이스** | 서버를 재시작하거나 다시 배포해도 글·댓글·채팅이 하나도 사라지지 않는다. |
| **로그인** | 모든 글쓰기, 에이전트 만들기, AI 호출이 로그인한 계정에 기록된다. 다른 사람의 이름으로는 쓸 수 없다. |
| **사용량 제한** | 한 사용자가 운영자의 AI 비용을 정해진 한도 이상 쓸 수 없다. 하루 전체 비용 상한을 넘으면 AI 호출이 자동으로 멈춘다. |

**이번 범위가 아닌 것** (3단계 이후): 결제와 유료 요금제, 서버 여러 대로 늘리기, Telegram 웹훅 전환, 본격적인 신고·관리자 도구 (이번에는 최소 기능만).

**바꾸지 않는 것:** Streamlit 화면 구성, HF Spaces Docker 배포, 커넥터 프로토콜(`/api/agent/*`). 이미 받은 커넥터 파일은 수정 없이 계속 동작합니다.

## 2. 현재 구조의 문제

| 현재 | 문제 |
|---|---|
| `ChatStore`/`ForumStore`/`AgentHub`가 서버 메모리에 데이터를 두고, `history.py`가 1분마다 HF 데이터셋에 기록을 올림 | 재시작하면 최대 1분치가 사라질 수 있습니다. 시작할 때 기록 전체를 다시 읽어야 해서 데이터가 쌓일수록 느려집니다. 서버가 한 대일 때만 동작합니다. |
| 닉네임을 자유롭게 입력 | 다른 사람을 사칭할 수 있습니다. 개인 에이전트 이름(`닉네임_claude`)도 남이 선점할 수 있습니다. |
| AI 호출에 제한이 없음 | `@AI` 한 번, 포럼 글 하나(에이전트 수만큼 호출)가 모두 운영자 `HF_TOKEN` 비용으로 나갑니다. |
| 개인 에이전트 토큰을 폐기할 방법이 없음 | 토큰이 유출되면 막을 수 없습니다. |

## 3. 데이터베이스

### 3.1 선택: PostgreSQL (외부 관리형 서비스)
- HF Space 컨테이너의 디스크는 재시작하면 지워지므로, DB는 **외부 관리형 서비스**에 둡니다. 후보는 Supabase, Neon 등이고, 무료 플랜 한도는 가입 전에 확인해야 합니다.
- 접속 정보는 Space Secret `DATABASE_URL` 하나로 받습니다.
- 라이브러리는 **SQLAlchemy 2 + psycopg 3**를 씁니다. 로컬 개발과 테스트는 같은 코드를 **SQLite**로 돌립니다(`DATABASE_URL` 미설정 시).
- 스키마 변경 관리는 Alembic으로 합니다.

### 3.2 테이블

```
users            id PK, issuer, subject (UNIQUE(issuer, subject)), email, nickname UNIQUE,
                 role ('user'|'admin'), plan ('free'), created_at, banned_at NULL
rooms            name PK, created_by → users, created_at, ai_name NULL
messages         id PK, room → rooms, user_id NULL → users, author, text, is_ai, created_at
                 INDEX(room, id)
posts            id PK, user_id → users, title, body, created_at, hidden_at NULL
comments         id PK, post_id → posts, user_id NULL → users, author, text, is_ai, created_at
                 INDEX(post_id, id)
personal_agents  name PK, owner_id → users, cli, token_hash UNIQUE, created_at,
                 last_seen_at, revoked_at NULL
agent_tasks      id PK, agent → personal_agents, kind, room NULL, post_id NULL, prompt,
                 status ('queued'|'taken'|'done'), created_at, taken_at
usage            user_id → users, day DATE, kind ('ai_chat'|'forum_post'|'agent_call'), count
                 PK(user_id, day, kind)
telegram_links   telegram_user_id PK, user_id → users, linked_at
```

- `author`는 화면 표시용 스냅샷입니다(AI 이름 등). 사람이 쓴 글은 `user_id`로 계정과 연결됩니다.
- `user_id NULL`은 AI가 쓴 메시지와 댓글입니다.
- 접속자 표시(presence)는 휘발성이므로 지금처럼 메모리에 둡니다.

### 3.3 코드 변경 방식: 저장소 인터페이스는 유지하고 구현만 교체
`community.py`, `telegram_bot.py`, `api.py`가 쓰는 메서드(`add_message`, `messages`, `create_post`, `comments`, `enqueue`, `next_task` 등)의 이름과 의미는 그대로 두고, DB를 쓰는 구현으로 바꿉니다.

| 기존 | 변경 |
|---|---|
| `chat_store.ChatStore` | `stores/sql.py`의 `SqlChatStore` (presence만 메모리) |
| `forum_store.ForumStore` | `SqlForumStore` |
| `agent_hub.AgentHub` | `SqlAgentHub`: 작업 대기열을 `agent_tasks` 테이블에 둡니다. 롱폴링은 1초 간격 조회와 같은 프로세스 내 즉시 알림(Condition)을 함께 씁니다. |
| `history.py` (HF 데이터셋) | 삭제합니다. 기존 기록은 1회 이전 스크립트로 옮깁니다(3.4). |

- 화면은 2초마다 새로고침하며 `WHERE room=? AND id > ?` 형태로 새 메시지만 읽습니다. 동시 접속 100명이면 초당 약 50회 조회이고, 인덱스가 있으면 문제없는 수준입니다.
- 기존 테스트는 SQLite DB로 그대로 돌려 같은 동작을 보장합니다.

### 3.4 기존 데이터 이전
`scripts/import_hf_history.py`를 만듭니다. HF 데이터셋의 JSONL 기록(`room`, `message`, `ai_name`, `post`, `comment`, `personal_agent`)을 읽어 DB에 넣습니다.
- 기존 글에는 계정이 없으므로 `user_id NULL`, `author=닉네임`으로 보존합니다.
- 기존 개인 에이전트는 주인 계정을 알 수 없으므로 **폐기 상태로 이전**합니다. 사용자는 로그인한 뒤 다시 만들어야 합니다.

## 4. 로그인

### 4.1 방식: Streamlit 내장 로그인 `st.login` (OIDC)
- Streamlit 1.66의 `st.login`/`st.user`/`st.logout`을 씁니다(이 저장소 환경에서 존재 확인). 의존성으로 `Authlib`가 추가됩니다.
- **OIDC를 지원하는 로그인만 붙일 수 있습니다.**
  - Google: 지원합니다.
  - 카카오: OIDC 옵션이 있다고 알려져 있으나 설정 전에 확인이 필요합니다.
  - 네이버: OIDC가 아닌 OAuth 2.0 방식으로 알려져 있어, `st.login`으로는 붙이기 어려울 것으로 예상합니다(확인 필요).
- **HF Spaces 주의 1:** `st.login` 설정은 `secrets.toml`의 `[auth]` 항목에서만 읽습니다. 환경변수로는 넣을 수 없으므로, 컨테이너가 시작할 때 Space Secrets(환경변수)로 `.streamlit/secrets.toml`을 만들어 주는 시작 스크립트를 추가합니다. 대상 값은 `AUTH_REDIRECT_URI`, `AUTH_COOKIE_SECRET`, `AUTH_GOOGLE_CLIENT_ID` 등입니다.
- **HF Spaces 주의 2:** huggingface.co의 Space 페이지는 앱을 iframe 안에 띄우는데, Google 로그인 화면은 iframe 안에서 열리지 않을 수 있습니다. 그래서 로그인 버튼은 새 창으로 `https://<space>.hf.space`를 열도록 안내하고, `redirect_uri`도 이 주소 기준으로 설정합니다. 실제 동작은 구현 단계에서 확인해야 합니다.

### 4.2 흐름
1. 로그인하지 않은 방문자는 **읽기만** 할 수 있습니다(채팅과 포럼 열람). 입력창 대신 "로그인하고 참여하기" 버튼이 보입니다.
2. 처음 로그인하면 **닉네임을 한 번 정합니다**. 닉네임은 다른 사람과 겹칠 수 없고, @멘션과 에이전트 이름에 쓰입니다.
3. 이후 사이드바의 닉네임 입력란은 사라지고, 로그인한 계정의 닉네임이 자동으로 쓰입니다.
4. `banned_at`이 설정된 계정은 읽기만 할 수 있습니다.

### 4.3 개인 에이전트
- 만들기, 목록 보기, **폐기**는 주인 계정만 할 수 있습니다. 🔌 탭에 "내 에이전트" 목록과 폐기 버튼을 추가합니다.
- 토큰이 유출되면 폐기 후 다시 만들면 됩니다(`revoked_at`을 기록하면 이후 그 토큰은 401).
- 에이전트 이름은 `닉네임_cli`입니다. 닉네임이 고유하므로 남이 이름을 선점할 수 없습니다.

### 4.4 Telegram 계정 연결
- 웹에서 "Telegram 연결"을 누르면 10분간 유효한 6자리 코드가 나옵니다. 사용자가 봇에 `/link 123456`을 보내면 Telegram 계정과 웹 계정이 연결됩니다(`telegram_links`).
- 연결하지 않은 Telegram 사용자는 `/posts` 같은 **읽기 명령만** 쓸 수 있습니다. 글쓰기와 AI 호출은 연결한 뒤에 가능합니다.
- Telegram으로 쓴 사용량도 같은 계정의 한도에 합산됩니다.

## 5. 사용량 제한

### 5.1 무엇에 비용이 드나
| 동작 | 비용을 내는 쪽 | 제한 기준 |
|---|---|---|
| 채팅 `@AI` 호출 | 운영자 (`HF_TOKEN`) | 사용자당 하루 횟수 |
| 포럼 새 글 (기본 에이전트 수만큼 AI 호출) | 운영자 | 사용자당 하루 글 수 |
| 포럼 `@Critic` 등 기본 에이전트 호출 | 운영자 | `ai_chat`과 같은 한도에 합산 |
| 개인 에이전트 호출 | **그 에이전트 주인** | 주인을 보호하기 위해 에이전트당 시간당 호출 수 |
| 일반 메시지와 댓글 | 없음 (스팸만 문제) | 분당 메시지 수 |

### 5.2 기본 한도 (초안 — 운영자가 정해야 합니다)
| 항목 | free 플랜 | 설정 이름 |
|---|---|---|
| `@AI`·기본 에이전트 호출 | 하루 30회 | `LIMIT_AI_PER_DAY` |
| 포럼 새 글 | 하루 5개 | `LIMIT_POSTS_PER_DAY` |
| 개인 에이전트가 받는 호출 | 에이전트당 시간당 20회 | `LIMIT_AGENT_CALLS_PER_HOUR` |
| 메시지·댓글 | 분당 20개 | `LIMIT_MESSAGES_PER_MIN` |
| **서비스 전체 AI 호출** | 하루 2,000회 (넘으면 모든 AI 호출 중지) | `GLOBAL_AI_PER_DAY` |

- 한도를 넘으면 AI를 호출하지 않고 "오늘 AI 사용 한도(30회)를 다 썼습니다. 내일 다시 이용해 주세요" 같은 안내를 AI 자리에 표시합니다.
- 전체 상한에 걸리면 운영자에게 Telegram으로 알림을 보냅니다(`ADMIN_TELEGRAM_CHAT_ID` 설정 시).
- `users.plan` 값과 `limits.py`의 플랜별 한도표로 관리합니다. 유료 플랜은 3단계에서 이 표에 행을 추가하는 것으로 확장합니다.

### 5.3 구현
- `limits.py`의 `check_and_count(user_id, kind) -> bool`는 `usage` 테이블에 오늘 카운트를 원자적으로 올리고(UPSERT), 한도 안이면 True를 반환합니다. AI를 **호출하기 전에** 검사합니다.
- 분당 메시지 제한은 정확도가 덜 중요하므로 메모리 슬라이딩 윈도우로 처리합니다.
- HF 응답의 토큰 사용량(`usage`)을 `messages`/`comments`와 함께 기록해 두면, 나중에 실제 비용을 분석하고 한도를 조정하는 데 씁니다. 화면에는 보여주지 않습니다.

## 6. 최소 관리 기능
- `role='admin'`인 계정에는 글과 메시지마다 **숨기기** 버튼, 사용자 **차단** 버튼이 보입니다.
- 일반 사용자는 **신고** 버튼만 씁니다. 신고는 `reports` 테이블에 쌓이고, 관리자가 사이드바에서 확인합니다.
- 첫 관리자는 Space Secret `ADMIN_EMAILS`(쉼표 구분)로 지정합니다.

## 7. 파일 변경 계획

| 파일 | 변경 |
|---|---|
| `db.py` (신규) | SQLAlchemy 엔진·세션, 테이블 정의 |
| `stores/sql.py` (신규) | `SqlChatStore`, `SqlForumStore`, `SqlAgentHub` |
| `limits.py` (신규) | 플랜별 한도표, `check_and_count` |
| `auth.py` (신규) | `st.user` → `users` 행 조회와 생성, 닉네임 등록, 관리자 판정 |
| `community.py` | 메서드에 `user` 인자 추가, AI 호출 전에 한도 검사 |
| `ui.py` | 로그인과 닉네임 등록 화면, 읽기 전용 모드, 내 에이전트 폐기, 신고와 숨기기 버튼 |
| `telegram_bot.py` | `/link`, 연결 여부에 따른 권한, 한도 적용 |
| `api.py` | 폐기된 토큰이면 401 (프로토콜은 그대로) |
| `state.py` | 저장소 구현 선택 (`DATABASE_URL`), `history.py` 연결 제거 |
| `entrypoint.sh` (신규), `Dockerfile` | 시작할 때 환경변수로 `secrets.toml [auth]`를 만들고 DB 마이그레이션을 실행 |
| `scripts/import_hf_history.py` (신규) | 기존 HF 데이터셋 기록 이전 |
| `history.py`, `chat_store.py`, `forum_store.py`, `agent_hub.py` | DB 구현이 같은 테스트를 통과하면 삭제 (presence 부분은 이전) |
| `requirements.txt` | `sqlalchemy`, `psycopg[binary]`, `alembic`, `authlib` 추가 |

## 8. 진행 순서와 확인 방법

| 단계 | 작업 | 확인 |
|---|---|---|
| 1 | DB 저장소 구현 (SQLite로 개발) | 기존 테스트 46개가 DB 구현으로도 모두 통과 |
| 2 | 로그인과 닉네임 등록, 읽기 전용 모드 | 로그인하지 않으면 입력창이 없음, 닉네임 중복이 거부됨 (테스트 + 로컬 Google 로그인) |
| 3 | 사용량 제한 | 한도+1번째 호출에서 AI가 호출되지 않음, 전체 상한이 동작 (테스트) |
| 4 | 개인 에이전트 주인 연결과 폐기, Telegram `/link` | 폐기한 토큰은 401, 연결하지 않은 Telegram 사용자는 쓰기가 거부됨 (테스트) |
| 5 | 관리 기능 (숨기기, 차단, 신고) | 숨긴 글이 일반 사용자에게 보이지 않음 (테스트) |
| 6 | HF Space 배포 (외부 PostgreSQL 연결, 기존 기록 이전) | 재시작 전후로 데이터가 같음, hf.space 주소에서 Google 로그인이 동작 |

각 단계는 별도 커밋으로 진행하고, 단계마다 결과를 보고한 뒤 다음으로 넘어갑니다.

## 9. 위험과 미확인 사항
- **HF Space에서의 로그인:** iframe 안에서 Google 로그인이 되는지, `redirect_uri` 설정이 그대로 동작하는지는 실제 배포에서 확인해야 합니다. 안 되면 사용자가 hf.space 주소로 직접 접속하도록 안내합니다.
- **카카오·네이버 로그인:** `st.login`이 쓰는 OIDC 방식을 지원하는지 확인이 필요합니다. 지원하지 않으면 별도 OAuth 연동이 필요해 작업량이 늘어납니다.
- **외부 DB 무료 플랜 한도와 지연:** HF Space와 DB 서버의 지역이 다르면 2초마다 하는 새로고침이 느려질 수 있습니다. 같은 지역의 DB를 고르는 것이 좋습니다.
- **개인정보:** 이메일 등 개인정보를 수집하게 되므로 개인정보 처리방침과 이용약관이 필요합니다. 법률 검토 대상이며, 이 설계에는 포함하지 않았습니다.
- **개인 구독 CLI의 상업적 이용:** 사용자의 Claude/ChatGPT/SuperGrok 구독으로 다른 사람의 메시지에 답하는 기능을 상용 서비스에서 제공해도 되는지는 각사 약관 확인이 필요합니다(미검토).

## 10. 결정이 필요한 사항
1. **로그인 수단:** Google만 할지, 카카오(확인 후)나 GitHub도 붙일지
2. **DB 서비스:** Supabase, Neon, 기타 중 어느 것을 쓸지 (계정 생성은 운영자가 직접)
3. **5.2의 한도 숫자**
4. **로그인 전 열람 허용 여부:** 허용(설계안 기본값)할지, 로그인해야만 보이게 할지
5. **기존 데이터 처리:** 이전(설계안 기본값)할지, 새로 시작할지
