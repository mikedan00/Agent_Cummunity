// Browser scenario: peers whose network blocks direct WebRTC links fall back to the lobby relay.
// Usage: uvicorn lobby:app --port 7870 &  then  node tests/browser/relay.e2e.js [screenshot dir]
const { chromium, devices } = require('playwright');
const URL = process.env.LOBBY_URL || 'http://localhost:7870';
const OUT = process.argv[2] || '.';
const errors = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const step = (t) => console.log('✓', t);

// No usable ICE candidates at all: every direct link from this browser fails, like a strict NAT.
const BLOCK_DIRECT = `(() => {
  const Orig = window.RTCPeerConnection;
  window.RTCPeerConnection = function (config) { return new Orig({ ...(config || {}), iceServers: [], iceTransportPolicy: "relay" }); };
  window.RTCPeerConnection.prototype = Orig.prototype;
})();`;

async function join(b, nick, { mobile = false, blocked = false } = {}) {
  const ctx = await b.newContext(mobile ? { ...devices['iPhone 13'] } : { viewport: { width: 1200, height: 850 } });
  if (blocked) await ctx.addInitScript(BLOCK_DIRECT);
  const p = await ctx.newPage();
  p.on('pageerror', (e) => errors.push(`${nick}: ${e.message}`));
  p.on('dialog', (d) => d.accept());
  await p.goto(URL);
  await p.locator('#nickname').fill(nick);
  await p.locator('#join-default').click();
  await p.locator('#send').waitFor({ state: 'visible', timeout: 40000 }); // admitted (through the relay if blocked)
  return p;
}
async function say(p, text) { await p.locator('#send textarea').fill(text); await p.locator('#send textarea').press('Enter'); }
const sees = (p, text, timeout = 8000) => p.locator('#messages').getByText(text).waitFor({ timeout });
const peers = (p) => p.locator('#peers').innerText();

(async () => {
  const b = await chromium.launch();
  const A = await join(b, '철수');
  const P = await join(b, '폰사용자', { mobile: true, blocked: true });
  step('blocked phone admitted through the lobby relay; phone sees ' + JSON.stringify(await peers(P)));
  if (!(await peers(P)).includes('📡')) throw new Error('expected a 📡 relayed link');

  await say(A, 'PC에서 보냄'); await sees(P, 'PC에서 보냄');
  await say(P, '폰에서 보냄'); await sees(A, '폰에서 보냄');
  step('messages both ways over the relay');

  const B = await join(b, '영희');
  await sleep(2000);
  step('third member joined; 영희 sees ' + JSON.stringify(await peers(B)));
  await say(P, '모두에게'); await sees(B, '모두에게');

  await P.getByRole('button', { name: '📝 포럼' }).click();
  await P.getByText('✏️ 새 글 쓰기').click();
  await P.getByPlaceholder('제목').fill('중계로 쓴 글');
  await P.getByPlaceholder('아이디어를 적어 주세요').fill('직접 연결이 막혀도 됩니다');
  await P.getByRole('button', { name: '등록' }).click();
  await B.getByRole('button', { name: '📝 포럼' }).click();
  await B.locator('#posts').getByText('중계로 쓴 글').waitFor({ timeout: 8000 });
  step('forum post from the blocked phone replicated');
  await P.getByRole('button', { name: '💬 채팅' }).click();
  await B.getByRole('button', { name: '💬 채팅' }).click();

  for (let i = 0; i < 40; i++) await say(A, `기록 채우기 ${i} ` + '가나다라마바사'.repeat(10));
  await sees(P, '기록 채우기 39');

  // Let every pair settle (direct or 📡) before the host leaves, as a real room would have.
  const settled = () => [...room.mesh.peers.values()].every((x) => x.info && room.mesh.isOpen(x));
  await P.waitForFunction(`(${settled})()`, null, { timeout: 20000 });
  await B.waitForFunction(`(${settled})()`, null, { timeout: 20000 });
  step('links settled; phone sees ' + JSON.stringify(await peers(P)));
  await A.context().close();
  await sees(B, '이제 내가 호스트입니다', 20000);
  await sees(P, '영희님이 호스트를 이어받았습니다', 20000);
  step('host handed over to 영희; the phone follows over the relay');

  // A second blocked newcomer gets the (chunked) history through the relay from the new host.
  const D = await join(b, '민수', { blocked: true });
  await sees(D, '기록 채우기 0', 15000);
  await sees(D, 'PC에서 보냄', 15000);
  step('blocked newcomer got the chunked history: ' + (await D.locator('#messages .msg').count()) + ' items');
  await say(D, '막힌 둘이서도'); await sees(P, '막힌 둘이서도');
  step('two blocked browsers talk through the lobby');

  await P.screenshot({ path: `${OUT}/v4_relay_phone.png` });
  console.log('page errors:', JSON.stringify(errors));
  await b.close();
  if (errors.length) process.exit(1);
})().catch((e) => { console.error('FAILED', e.message, '\npage errors:', JSON.stringify(errors)); process.exit(1); });
