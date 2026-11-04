// Browser scenario for AI in the P2P room, with a real connector bridge.
// Usage: uvicorn lobby:app --port 7870 &
//        python agent_connector.py --bridge --allow-origin http://localhost:7870 &   (prints the code)
//        BRIDGE_CODE=xxxx node tests/browser/ai.e2e.js [screenshot dir]
const { chromium } = require('playwright');
const URL = process.env.LOBBY_URL || 'http://localhost:7870';
const CODE = process.env.BRIDGE_CODE;
const OUT = process.argv[2] || '.';
const errors = [];
const step = (t) => console.log('✓', t);

async function join(b, nick, room = 'lobby') {
  const ctx = await b.newContext({ viewport: { width: 1200, height: 900 } });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => errors.push(`${nick}: ${e.message}`));
  p.on('dialog', (d) => d.accept());
  await p.goto(URL);
  await p.locator('#nickname').fill(nick);
  if (room === 'lobby') await p.locator('#join-default').click();
  else await p.locator('#create-room').click();
  await p.locator('#send').waitFor({ state: 'visible', timeout: 15000 });
  return p;
}
async function connectBridge(p) {
  await p.getByRole('button', { name: '🤖 AI' }).click();
  await p.locator('#bridge-code').fill(CODE);
  await p.getByRole('button', { name: '연결', exact: true }).click();
  await p.getByText('✅ 연결됨').waitFor({ timeout: 15000 });
}
const tabTo = (p, name) => p.getByRole('button', { name }).click();

(async () => {
  if (!CODE) throw new Error('set BRIDGE_CODE');
  const b = await chromium.launch();

  // 1. Host connects its CLI; @AI from a guest is answered through the host's Claude Code.
  const host = await join(b, '호스트');
  await connectBridge(host);
  await host.getByText('사용 중: Claude Code').waitFor();
  step('host bridge connected, AI source = Claude Code');
  await tabTo(host, '💬 채팅');
  const guest = await join(b, '손님');
  await guest.waitForTimeout(1500);
  if (await guest.locator('#messages').getByText('이제 내가 호스트입니다').count()) throw new Error('guest announced itself as host');
  await guest.locator('#send textarea').fill('@AI 한 문장으로 짧게 인사해 주세요');
  await guest.locator('#send textarea').press('Enter');
  const aiMsg = guest.locator('#messages .msg.ai', { hasText: 'AI · Claude Code' });
  await aiMsg.waitFor({ timeout: 120000 });
  step('@AI answered: ' + (await aiMsg.locator('.text').innerText()).slice(0, 80));

  // 2. A new forum post gets comments from Optimist and Critic.
  await tabTo(guest, '📝 포럼');
  await guest.getByText('✏️ 새 글 쓰기').click();
  await guest.getByPlaceholder('제목').fill('동네 도서관 앱');
  await guest.getByPlaceholder('아이디어를 적어 주세요').fill('책 대출 현황을 앱으로 알려주면 어떨까요? 한 문장으로만 의견 주세요.');
  await guest.getByRole('button', { name: '등록' }).click();
  await guest.locator('#comments .msg.ai', { hasText: 'Optimist' }).waitFor({ timeout: 180000 });
  await guest.locator('#comments .msg.ai', { hasText: 'Critic' }).waitFor({ timeout: 180000 });
  step('forum agents Optimist and Critic commented');

  // 3. A participant joins with their own agent; a mention is answered from their tab.
  const owner = await join(b, '민수');
  await connectBridge(owner);
  await owner.locator('#agent-cli').selectOption('claude');
  await owner.getByRole('button', { name: '적용' }).click();
  await owner.getByText('@민수_claude 로 참여 중').waitFor();
  await tabTo(owner, '💬 채팅');
  await guest.locator('#agents').getByText('@민수_claude').waitFor({ timeout: 10000 });
  await tabTo(guest, '💬 채팅');
  await guest.locator('#send textarea').fill('@민수_claude 한 단어로 대답해 주세요: 하늘은 무슨 색?');
  await guest.locator('#send textarea').press('Enter');
  const agentMsg = guest.locator('#messages .msg.ai', { hasText: '민수_claude' });
  await agentMsg.waitFor({ timeout: 120000 });
  step('personal agent answered: ' + (await agentMsg.locator('.text').innerText()).slice(0, 80));

  // 4. A host without any CLI is asked to connect an HF token.
  const lone = await join(b, '혼자', 'new');
  await lone.locator('#ai-banner').getByText('HF 토큰 연결하기').waitFor({ timeout: 10000 });
  step('host without CLI sees the HF token prompt');

  await guest.screenshot({ path: `${OUT}/v4_ai_chat.png` });
  await host.getByRole('button', { name: '🤖 AI' }).click();
  await host.screenshot({ path: `${OUT}/v4_ai_tab.png`, fullPage: true });
  console.log('page errors:', JSON.stringify(errors));
  await b.close();
  if (errors.length) process.exit(1);
})().catch((e) => { console.error('FAILED', e.message, '\npage errors:', JSON.stringify(errors)); process.exit(1); });
