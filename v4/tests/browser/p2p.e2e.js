const { chromium, devices } = require('playwright');
// Usage: uvicorn lobby:app --port 7870 &  then  node tests/browser/p2p.e2e.js [screenshot dir]
const URL = process.env.LOBBY_URL || 'http://localhost:7870';
const errors = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function person(b, nick, mobile) {
  const ctx = await b.newContext(mobile ? { ...devices['iPhone 13'] } : { viewport: { width: 1200, height: 850 } });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => errors.push(`${nick}: ${e.message}`));
  await p.goto(URL);
  await p.locator('#nickname').fill(nick);
  await p.locator('#join-default').click();
  await p.locator('#status').getByText('로비 연결됨').waitFor();
  return p;
}
const peers = (p) => p.locator('#peers').innerText();
const feed = (p) => p.locator('#messages').innerText();
(async () => {
  const b = await chromium.launch();
  const A = await person(b, '철수', false);
  const C = await person(b, '폰사용자', true);   // joins second, but phones are not preferred as host
  const B = await person(b, '영희', false);
  await sleep(2500);
  console.log('A peers:', JSON.stringify(await peers(A)));
  console.log('C peers:', JSON.stringify(await peers(C)));

  await A.locator('#send textarea').fill('안녕하세요 모두');
  await A.locator('#send textarea').press('Enter');
  await B.locator('#messages').getByText('안녕하세요 모두').waitFor({ timeout: 5000 });
  await C.locator('#messages').getByText('안녕하세요 모두').waitFor({ timeout: 5000 });
  console.log('A -> B, C delivered directly');

  // Cut the direct B<->C link: B's messages must still reach C through A (flooding).
  await B.evaluate(() => { const [, p] = [...mesh.peers.entries()].find(([, p]) => p.info && p.info.nickname === '폰사용자'); p.pc.close(); });
  await sleep(300);
  console.log('B peers after cut:', JSON.stringify(await peers(B)));
  await B.locator('#send textarea').fill('C에게 A를 거쳐 가는 메시지');
  await B.locator('#send textarea').press('Enter');
  await C.locator('#messages').getByText('C에게 A를 거쳐 가는 메시지').waitFor({ timeout: 5000 });
  console.log('B -> C delivered via A after direct link was cut');

  await sleep(6000); // the newer of B/C retries a direct link through the lobby
  console.log('B peers after repair:', JSON.stringify(await peers(B)));
  // Host (A) leaves: the next host must be 영희 (PC) even though 폰사용자 joined earlier.
  await A.context().close();
  await B.locator('#messages').getByText('이제 내가 호스트입니다').waitFor({ timeout: 20000 });
  await C.locator('#messages').getByText('영희님이 호스트를 이어받았습니다').waitFor({ timeout: 20000 });
  console.log('host handed over to 영희; B peers:', JSON.stringify(await peers(B)));

  // B and C reconnect? They dropped the direct link; a new join must still work, and history must sync.
  const D = await person(b, '민수', false);
  await D.locator('#messages').getByText('안녕하세요 모두').waitFor({ timeout: 8000 });
  console.log('newcomer 민수 got history from host');
  await D.locator('#send textarea').fill('저 왔어요');
  await D.locator('#send textarea').press('Enter');
  await C.locator('#messages').getByText('저 왔어요').waitFor({ timeout: 5000 });
  await B.locator('#messages').getByText('저 왔어요').waitFor({ timeout: 5000 });
  console.log('민수 -> 영희, 폰사용자 delivered');
  await B.screenshot({ path: (process.argv[2] || '.') + '/v4_pc.png' });
  await C.screenshot({ path: (process.argv[2] || '.') + '/v4_phone.png' });
  console.log('errors:', JSON.stringify(errors));
  await b.close();
})().catch((e) => { console.error('FAILED', e.message, 'errors:', JSON.stringify(errors)); process.exit(1); });
