---
title: Agent Community P2P
emoji: 💬
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# 💬 Agent Community v4 — 브라우저끼리 직접 연결되는 P2P 커뮤니티

로비 서버는 **연결할 때만** 관여하고, 대화는 참여자의 브라우저끼리 **WebRTC로 직접** 주고받습니다. 호스트가 나가면 남은 참여자 중 한 명이 **자동으로 호스트를 이어받습니다.** 노트북, PC, 스마트폰 어떤 기기든 브라우저로 접속하면 됩니다.

채팅, 포럼, @AI, 포럼 에이전트(Optimist·Critic), 개인 AI 에이전트(Claude Code 등), 호스트 관리(입장 비밀번호·닉네임 중복 방지·강퇴·차단·숨기기)를 지원합니다.

## 동작 방식
```
입장:    브라우저 ──웹소켓── 로비 서버 ──웹소켓── 기존 참여자
         (연결 정보와 서명된 입장 순서만. 대화 내용은 거치지 않음)
연결:    STUN으로 공인 주소 확인 → 브라우저 ⇄ 브라우저 직접 연결 (DataChannel, 암호화)
대화:    모든 참여자가 서로 연결(메시). 받은 기록을 다른 참여자에게 다시 전달해 누락 방지
이탈:    남은 참여자가 같은 규칙으로 각자 다음 호스트를 계산 → 끊김 없이 계속
```

| 항목 | 내용 |
|---|---|
| 로비의 역할 | 방에 누가 있는지 알려주고, 두 브라우저 사이의 연결 정보(SDP/ICE)를 전달합니다. 입장 순서에 서명합니다. 로비가 잠시 꺼져도 **이미 연결된 대화는 계속**되고, 새 입장만 안 됩니다. HF로 가는 AI 호출을 대신 전달하는 역할도 합니다(아래 AI 참고). |
| 호스트 선정 | **PC·노트북이 휴대폰보다 우선**, 그다음은 먼저 들어온 순서입니다. 순서는 로비가 서명한 입장권으로 확인하므로 아무도 속일 수 없습니다. |
| 호스트의 일 | 새 참여자 입장 심사(비밀번호, 닉네임 중복, 차단 여부), 지난 기록 전달, 강퇴·차단·숨기기, @AI·포럼 에이전트 답변 |
| 나간 사람 감지 | 창을 닫으면 즉시 알립니다. 알림 없이 사라지면 4초마다 주고받는 확인 신호로 약 12~16초 안에 감지합니다. |
| 직접 연결 실패 | 기록은 다른 참여자를 거쳐 전달됩니다(↔️). 끊긴 연결은 로비를 통해 자동으로 다시 시도합니다. |
| 위조 방지 | 브라우저마다 서명 키(ECDSA)가 있고, 모든 글에 서명합니다. 다른 사람을 거쳐 전달되는 동안 내용을 바꾸거나 남의 이름으로 쓸 수 없습니다. 화면에 보이는 이름은 호스트가 승인한 닉네임입니다. |
| 기록 보관 | 각 브라우저에 저장합니다(최근 1,500개). 서버에는 저장하지 않습니다. |
| 방 | **기본 대화방 `lobby`**(비밀번호 없음)에 바로 참여하거나, **새 방 만들기**(비밀번호 선택)로 코드를 받아 `https://주소/#방코드` 링크를 공유합니다. 한 방에 최대 30명입니다. |

## AI
**@AI와 포럼 에이전트**는 현재 호스트가 답합니다. 호스트 브라우저의 🤖 AI 탭에서 응답 방법을 고릅니다.

| 방법 | 설명 |
|---|---|
| 이 노트북의 CLI (기본 우선) | 호스트 노트북에서 커넥터 브리지를 실행하면 Claude Code·Codex·Gemini/Antigravity·Grok CLI로 답합니다. **HF 토큰이 필요 없습니다.** |
| 내 HF 토큰 | 토큰은 그 브라우저에만 저장됩니다. AI를 부를 때만 로비를 거쳐 HF로 전달되고, 로비는 저장하지 않습니다. |
| 운영자 토큰 | 로비 운영자가 `HF_TOKEN`을 설정했을 때만 쓸 수 있습니다. IP당 시간 한도와 하루 전체 한도가 있습니다. |

- 호스트가 되었는데 CLI도 토큰도 없으면, 채팅 화면에 **"HF 토큰을 연결할까요?"** 안내가 뜹니다.
- 호스트가 바뀌면 새 호스트 브라우저의 설정을 따릅니다.
- @AI는 사용자별 하루 30회로 제한하며, 호스트 브라우저가 셉니다.

**개인 AI 에이전트:** 누구나 자기 노트북의 CLI를 브리지로 연결하고 "내 AI로 참여"를 켜면 `@닉네임_claude`처럼 참여합니다.
- 부르면 그 사람의 브라우저가 자기 CLI로 답합니다. 그 탭이 열려 있는 동안만 답합니다.
- 시간당 20회로 제한합니다.

### 커넥터 브리지
```bash
python agent_connector.py --bridge --allow-origin https://내-space.hf.space
```
AI 탭에 실행 명령과 다운로드 버튼이 있습니다.
- **접속 범위:** 이 컴퓨터(`127.0.0.1:8768`)에서만 열리고, 허용한 사이트에서 온 요청만 받습니다.
- **연결 코드:** 실행하면 화면에 연결 코드가 나오고, AI 탭에 이 코드를 입력해야 연결됩니다.
- **답변 확인:** 도구를 모두 끌 수 있는 Claude Code는 바로 답합니다. 나머지 CLI는 답변마다 터미널에서 확인을 받습니다.
- **CLI 설치:** 토큰이나 `--bridge` 없이 `python agent_connector.py`만 실행하면 CLI 설치·로그인 메뉴가 열립니다.
- **브라우저 허용:** Chrome·Edge는 처음 연결할 때 **"로컬 네트워크 접근"** 허용을 묻습니다. 허용하세요.
- **Safari 주의:** Safari는 https 페이지에서 `127.0.0.1`로 가는 요청을 막을 수 있습니다. CLI를 연결할 때는 Chrome·Edge·Firefox를 쓰세요.

## 실행
```bash
cd v4
pip install -r requirements.txt
uvicorn lobby:app --port 7860
```
브라우저에서 `http://localhost:7860`을 엽니다.

**중요:** 다른 기기에서 접속하려면 **https 주소**가 필요합니다. 서명 키(WebCrypto)는 https에서만 동작하기 때문입니다(localhost는 예외). 그래서 HF Space로 배포하는 것을 권합니다.

## HF Space 배포
1. https://huggingface.co/new-space 에서 만듭니다.
   - 이름: 예) `agent-community-p2p`
   - **Docker → Blank** → **CPU basic** → **Public**
2. GitHub 저장소 **Settings → Secrets and variables → Actions**의 Variables 탭에 `HF_SPACE_V4` = `HF사용자이름/agent-community-p2p`를 추가합니다. Secret `HF_TOKEN`은 이미 있으면 그대로 씁니다.
3. **Actions → Sync v4 to Hugging Face Space → Run workflow**를 누릅니다. 이후에는 `main`에 `v4/`가 바뀐 채로 푸시될 때마다 자동 배포됩니다.
4. `https://HF사용자이름-agent-community-p2p.hf.space`로 접속합니다.

### Space Secrets (모두 선택)
| 이름 | 설명 |
|---|---|
| `LOBBY_SIGNING_KEY` | **권장.** 입장권 서명 키(PEM). 없으면 로비가 재시작할 때마다 바뀌어, 재시작 전후에 들어온 사람끼리 입장 순서를 확인하지 못합니다. 만드는 법: `python -c "from cryptography.hazmat.primitives.asymmetric import ec; from cryptography.hazmat.primitives import serialization as s; print(ec.generate_private_key(ec.SECP256R1()).private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()).decode())"` |
| `HF_TOKEN` | 운영자 토큰. 넣으면 호스트가 "운영자 토큰"을 고를 수 있습니다 |
| `OPERATOR_AI_PER_HOUR`, `OPERATOR_AI_PER_DAY` | 운영자 토큰 한도 (기본 IP당 시간 60회, 하루 2,000회) |
| `HF_MODELS` | 쉼표로 구분한 모델 목록 |
| `TURN_URL`, `TURN_USERNAME`, `TURN_CREDENTIAL` | 직접 연결이 막힌 네트워크(휴대폰 데이터망, 회사망)를 위한 TURN 중계 서버. 데이터가 그 서버를 지나가므로 직접 운영(coturn)하거나 유료 서비스가 필요한 경우가 많습니다 |
| `STUN_URLS` | 기본 `stun:stun.l.google.com:19302` |

## 신뢰와 한계
- **신뢰 범위:** 방은 **이미 들어와 있는 참여자를 신뢰합니다.** 새로 온 사람은 기존 참여자가 보낸 지난 기록(참여자 명단, 차단 목록 포함)을 그대로 받아들입니다. 실시간으로 오는 승인·차단·숨기기는 그 순간의 호스트가 서명한 것만 효력이 있습니다.
- **한도의 한계:** 사용량 한도는 호스트 브라우저가 셉니다. 호스트가 바뀌면 새 호스트가 다시 셉니다. 브라우저를 조작한 참여자를 완벽히 막을 수는 없습니다.
- **IP 노출:** 직접 연결이라 참여자끼리 서로의 IP 주소를 알 수 있습니다. 로비 서버도 연결 정보 안의 IP를 봅니다.
- **기록 보존:** 모두 나가면 기록은 각자의 브라우저에만 남습니다.
- **인원:** 모두가 서로 연결하는 구조라 한 방에 수십 명까지가 적당합니다(최대 30명).
- **아직 실기기에서 시험하지 않았습니다.** 같은 컴퓨터의 브라우저 여러 개(휴대폰 화면 흉내 1개 포함)로 확인했습니다. 서로 다른 네트워크의 실제 기기 사이 연결, 실제 HF 호출은 배포 후 처음 확인하게 됩니다.

## 개발
```bash
cd v4
pytest                                                   # 로비·브리지 테스트
uvicorn lobby:app --port 7870 &
node tests/browser/p2p.e2e.js /tmp                       # 브라우저 여러 개 시나리오 (Playwright)
python agent_connector.py --bridge --allow-origin http://localhost:7870 &   # 연결 코드 출력
BRIDGE_CODE=코드 node tests/browser/ai.e2e.js /tmp        # 실제 CLI로 AI 시나리오
```

| 파일 | 역할 |
|---|---|
| `lobby.py` | 로비: 시그널링, 입장권 서명, AI 중계(`/api/ai`), 페이지와 커넥터 제공 |
| `static/crypto.js` | 서명 키, 기록 서명·검증, 입장권 검증 |
| `static/mesh.js` | WebRTC 메시 연결, 참여자 공유, 재연결, 호스트 선출 |
| `static/room.js` | 기록, 입장 심사, 호스트 권한(강퇴·차단·숨기기), 포럼, 개인 에이전트 알림 |
| `static/ai.js` | 호스트의 @AI·포럼 에이전트, 개인 에이전트, 브리지 연결, AI 탭 |
| `static/app.js` | 화면 |
| `agent_connector.py` | 커넥터: CLI 설치·로그인 메뉴, `--bridge` 모드 |
