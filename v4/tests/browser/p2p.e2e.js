// Browser scenario for the P2P room (Playwright, Chromium).
// Usage: uvicorn lobby:app --port 7870 &  then  node tests/browser/p2p.e2e.js [screenshot dir]
const { chromium, devices } = require('playwright');
const URL = process.env.LOBBY_URL || 'http://localhost:7870';
const OUT = process.argv[2] || '.';
const errors = [], dialogs = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const step = (text) => console.log('✓', text);

async function open(b, nick, mobile) {
  const ctx = await b.newContext(mobile ? { ...devices['iPhone 13'] } : { viewport: { width: 1200, height: 850 } });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => errors.push(`${nick}: ${e.message}`));
  p.on('dialog', (d) => { dialogs.push(`${nick}: ${d.message()}`); d.accept(); });
  await p.goto(URL);
  await p.locator('#nickname').fill(nick);
  return p;
}
async function joinLobby(b, nick, mobile) {
  const p = await open(b, nick, mobile);
  await p.locator('#join-default').click();
  await p.locator('#send').waitFor({ state: 'visible', timeout: 15000 }); // admitted
  return p;
}
async function say(p, text) { await p.locator('#send textarea').fill(text); await p.locator('#send textarea').press('Enter'); }
const sees = (p, text, timeout = 6000) => p.locator('#messages').getByText(text).waitFor({ timeout });
const peers = (p) => p.locator('#peers').innerText();

(async () => {
  const b = await chromium.launch();
  const A = await joinLobby(b, '철수', false);
  const C = await joinLobby(b, '폰사용자', true); // joins second, but phones are not preferred as host
  const B = await joinLobby(b, '영희', false);
  await sleep(2000);
  step('three admitted; A sees ' + JSON.stringify(await peers(A)));

  await say(A, '안녕하세요 모두');
  await sees(B, '안녕하세요 모두'); await sees(C, '안녕하세요 모두');
  step('A -> B, C delivered');

  // A forged record (someone else's key id, changed text) must be rejected by everyone.
  await C.evaluate(() => {
    const real = room.chat().find((r) => r.text === '안녕하세요 모두');
    const forged = { ...real, id: crypto.randomUUID(), text: '위조된 메시지' };
    room.mesh.broadcast({ type: 'rec', rec: forged });
  });
  await sleep(1500);
  if (await B.locator('#messages').getByText('위조된 메시지').count()) throw new Error('forged record was accepted');
  step('forged record rejected');

  // Duplicate nickname is refused by the host.
  const dup = await open(b, '영희', false);
  await dup.locator('#join-default').click();
  await sleep(4000);
  if (!dialogs.some((d) => d.includes('이미 쓰는 닉네임'))) throw new Error('duplicate nickname not refused: ' + JSON.stringify(dialogs));
  step('duplicate nickname refused');

  // Cut the direct B<->C link: messages still pass through A, and the link is repaired.
  await B.evaluate(() => { const [, p] = [...room.mesh.peers.entries()].find(([, p]) => p.info && room.nameOf(p.info.keyId) === '폰사용자'); p.pc.close(); });
  await sleep(300);
  await say(B, 'A를 거쳐 가는 메시지');
  await sees(C, 'A를 거쳐 가는 메시지');
  step('relayed through A after cutting B<->C');
  await sleep(6000);
  step('after repair: ' + JSON.stringify(await peers(B)));

  // Forum replicates.
  await B.getByRole('button', { name: '📝 포럼' }).click();
  await B.getByText('✏️ 새 글 쓰기').click();
  await B.getByPlaceholder('제목').fill('P2P 포럼 첫 글');
  await B.getByPlaceholder('아이디어를 적어 주세요').fill('브라우저끼리 복제됩니다');
  await B.getByRole('button', { name: '등록' }).click();
  await C.getByRole('button', { name: '📝 포럼' }).click();
  await C.locator('#posts').getByText('P2P 포럼 첫 글').click({ timeout: 6000 });
  await C.locator('#comment textarea').fill('폰에서 단 댓글');
  await C.locator('#comment textarea').press('Enter');
  await B.locator('#comments').getByText('폰에서 단 댓글').waitFor({ timeout: 6000 });
  step('forum post and comment replicated');
  await B.getByRole('button', { name: '💬 채팅' }).click();
  await C.getByRole('button', { name: '💬 채팅' }).click();

  // Host (A) leaves: the next host must be 영희 (PC) although 폰사용자 joined earlier.
  await A.context().close();
  await sees(B, '이제 내가 호스트입니다', 20000);
  await sees(C, '영희님이 호스트를 이어받았습니다', 20000);
  step('host handed over to 영희');

  // A newcomer is admitted by the new host and gets the history.
  const D = await joinLobby(b, '민수', false);
  await sees(D, '안녕하세요 모두', 8000);
  step('민수 admitted by the new host, history received');
  await say(D, '저 왔어요');
  await sees(C, '저 왔어요'); await sees(B, '저 왔어요');
  step('민수 -> 영희, 폰사용자 delivered');

  // The new host bans 민수: 민수 is removed and refused when rejoining.
  await B.locator('#peers li', { hasText: '민수' }).getByTitle('차단').click();
  await sleep(2000);
  if (!dialogs.some((d) => d.startsWith('민수: 호스트가 이 방에서 차단'))) throw new Error('ban not delivered: ' + JSON.stringify(dialogs));
  await D.waitForLoadState();
  await D.locator('#nickname').fill('민수');
  await D.locator('#join-default').click();
  await sleep(5000);
  if (!dialogs.some((d) => d.includes('차단되었습니다'))) throw new Error('banned member was not refused: ' + JSON.stringify(dialogs));
  step('ban removes and refuses again');

  // A password-protected room.
  const E = await open(b, '방장', false);
  await E.locator('#room-password').fill('secret12');
  await E.locator('#create-room').click();
  await E.locator('#send').waitFor({ state: 'visible', timeout: 10000 });
  const code = await E.locator('#room-name').innerText();
  const F = await open(b, '손님', false);
  await F.locator('#room-code').fill(code);
  await F.locator('#room-password').fill('wrong');
  await F.locator('#join-code').click();
  await sleep(4000);
  if (!dialogs.some((d) => d.includes('입장 비밀번호가 맞지 않습니다'))) throw new Error('wrong password not refused');
  await F.waitForLoadState();
  await F.locator('#nickname').fill('손님');
  await F.locator('#room-code').fill(code);
  await F.locator('#room-password').fill('secret12');
  await F.locator('#join-code').click();
  await F.locator('#send').waitFor({ state: 'visible', timeout: 10000 });
  step('password room: wrong refused, right admitted');

  await B.screenshot({ path: `${OUT}/v4_pc.png` });
  await C.screenshot({ path: `${OUT}/v4_phone.png` });
  console.log('page errors:', JSON.stringify(errors));
  await b.close();
  if (errors.length) process.exit(1);
})().catch((e) => { console.error('FAILED', e.message, '\npage errors:', JSON.stringify(errors), '\ndialogs:', JSON.stringify(dialogs)); process.exit(1); });
