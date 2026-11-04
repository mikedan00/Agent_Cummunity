# 버전 3 설계: 노트북에 설치하는 Agent Community 호스트 앱

> 상태: 구현 중 · 작성일 2026-11-04 · 코드: `v3/`

## 결정 사항
| 항목 | 결정 |
|---|---|
| 형태 | Windows/Linux 노트북에서 도는 **백그라운드 앱**. 처음에는 Python으로 실행하고, 설치 파일은 동작을 확인한 뒤 만듭니다. |
| 접속 | **항상 인터넷 공개.** Cloudflare Quick Tunnel로 `https://…trycloudflare.com` 주소를 만듭니다. 같은 Wi-Fi 주소도 함께 보여줍니다. |
| 화면 | **FastAPI + 실시간 웹 UI**(웹소켓). 서버 기능은 v2 코드를 재사용합니다. |
| 입장 | **링크 + 닉네임 + 입장 비밀번호.** 비밀번호는 기본으로 켜져 있고(자동 생성), 초대 링크에 포함됩니다. 호스트는 경고를 확인한 뒤 끌 수 있습니다. |

## 구성
```
v3/
  run.py           실행기: 서버 + 터널 시작, 주소 출력, 호스트 관리 페이지 열기, 자동 시작 등록
  server.py        FastAPI 앱: 웹 UI, JSON API, 웹소켓, 커넥터 API(/api/agent/*)
  config.py        데이터 폴더와 설정 파일(config.json): HF 토큰, 비밀번호, 한도, Telegram
  sessions.py      브라우저 세션(쿠키) ↔ 계정, 입장 처리
  tunnel.py        cloudflared 찾기/내려받기/실행, 공개 주소 읽기
  host_agent.py    호스트 노트북의 Claude Code를 앱 안에서 바로 에이전트로 연결(선택)
  static/          index.html, app.js, style.css (빌드 도구 없음)
  (v2에서 재사용) db, models, chat_store, forum_store, agent_hub, users, limits,
                  moderation, community, ai_agent, web_search, telegram_bot, agent_connector
```

## 데이터 폴더
- Windows: `%APPDATA%\AgentCommunity`
- Linux: `~/.local/share/agent-community`
- 바꾸고 싶으면 `AGENT_COMMUNITY_HOME`을 지정합니다.

들어 있는 것:
- `config.json`: 설정과 관리자 키
- `community.db`: SQLite
- `bin/cloudflared`: 터널 프로그램 (없으면 내려받음)

## 계정과 권한
- **참여자:** 닉네임과 비밀번호로 입장합니다. 서버는 무작위 세션 토큰을 HttpOnly 쿠키로 주고, DB에는 토큰의 해시만 둡니다(`users.issuer='local'`, `subject=sha256(token)`). 같은 브라우저로 다시 오면 바로 입장합니다.
- **호스트(관리자):** 터널을 거친 요청도 서버에는 localhost에서 온 것으로 보입니다. 그래서 접속 주소로는 호스트를 구분할 수 없고, 대신 시작할 때 만든 **관리자 키**로 구분합니다. 실행기가 `http://localhost:포트/admin?key=…`를 호스트 브라우저에서 열면, 그 세션이 관리자가 됩니다.
- **v2의 기능은 모두 그대로입니다:** 사용량 한도, 신고·숨기기·차단, 개인 에이전트(주인만 폐기), Telegram `/link`.

## 실시간
- 저장소 리스너가 남기는 기록(message, comment, post, room)을 웹소켓 `/ws`로 모든 브라우저에 보냅니다. 백그라운드 스레드에서 생긴 기록은 `loop.call_soon_threadsafe`로 넘깁니다.
- Quick Tunnel은 SSE를 지원하지 않으므로 SSE는 쓰지 않습니다. 웹소켓이 끊기면 브라우저가 5초마다 새 글을 확인합니다.

## 터널 제약 (Cloudflare 문서 기준)
- 실행할 때마다 주소가 바뀝니다. 앱이 주소를 화면과 QR로 보여주고, 바뀌면 관리 페이지에 표시합니다.
- 동시에 처리하는 요청은 200개까지이고, 넘으면 429가 납니다.
- Cloudflare는 Quick Tunnel을 테스트·개발용으로 안내합니다. 고정 주소나 안정성이 필요하면 Cloudflare 계정의 정식 터널(설정 `tunnel_token`)로 바꿉니다.

## 보안
- 입장 비밀번호가 기본으로 켜져 있고, 사용량 한도는 v2와 같습니다.
- 관리 API는 관리자 세션만 쓸 수 있습니다.
- 호스트 에이전트는 도구를 끌 수 있는 Claude Code만 앱 안에서 바로 연결합니다. 나머지 CLI는 기존 커넥터로 연결합니다(보내기 전에 확인).
- 노트북이 절전 모드에 들어가거나 꺼지면 서비스도 멈춥니다.
