// Page wiring for the P2P room. User text is always set as text, never as HTML.
"use strict";

const $ = (sel) => document.querySelector(sel);
const DEVICE = matchMedia("(pointer: coarse)").matches || /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)
  ? "mobile" : "pc";
let mesh = null;

function h(tag, cls, ...children) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
}

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3000);
}

const fmt = (ts) => new Date(ts).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
const storageKey = (room) => `agent-p2p:${room}`;

// ---- landing ----

$("#nickname").value = localStorage.getItem("agent-p2p:nickname") || "";
const fromHash = location.hash.replace(/^#/, "");
if (fromHash) $("#room-code").value = fromHash;

function nickname() {
  const n = $("#nickname").value.trim();
  if (!/^[0-9A-Za-z가-힣_]{2,20}$/.test(n)) {
    $("#landing-error").textContent = "닉네임은 2~20자의 한글, 영문, 숫자, _ 만 쓸 수 있습니다.";
    return null;
  }
  localStorage.setItem("agent-p2p:nickname", n);
  return n;
}

$("#join-default").addEventListener("click", () => enter("lobby"));
$("#join-code").addEventListener("click", () => enter($("#room-code").value.trim().toLowerCase()));
$("#create-room").addEventListener("click", async () => {
  const { room } = await (await fetch("/api/new-room")).json();
  enter(room);
});

// ---- room ----

function enter(room) {
  const nick = nickname();
  if (!nick) return;
  if (!/^[a-z0-9]{4,16}$/.test(room)) { $("#landing-error").textContent = "방 코드는 영문 소문자와 숫자 4~16자입니다."; return; }
  location.hash = room;
  $("#landing").hidden = true;
  $("#room").hidden = false;
  $("#room-name").textContent = room;
  $("#messages").replaceChildren();

  mesh = new Mesh(room, nick, DEVICE);
  try { // messages kept in this browser from earlier visits
    for (const m of JSON.parse(localStorage.getItem(storageKey(room)) || "[]")) mesh.store(m);
  } catch (e) { /* storage unavailable or corrupt: start empty */ }
  mesh.addEventListener("message", (e) => { render(e.detail); save(); });
  mesh.addEventListener("peers", renderPeers);
  mesh.addEventListener("joined", (e) => system(`${e.detail.nickname}님이 들어왔습니다.`));
  mesh.addEventListener("left", (e) => system(`${e.detail.nickname}님이 나갔습니다.`));
  mesh.addEventListener("host", (e) => system(e.detail.me ? "👑 이제 내가 호스트입니다." : `👑 ${e.detail.nickname}님이 호스트를 이어받았습니다.`));
  mesh.addEventListener("lobby", (e) => {
    $("#status").textContent = e.detail.open ? "로비 연결됨" : `로비 연결 끊김${e.detail.reason ? ` (${e.detail.reason})` : ""} — 대화는 계속되지만 새 입장은 안 됩니다`;
  });
  mesh.connect();
  renderPeers();
}

function render(m) {
  const feed = $("#messages");
  const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
  const nodes = [...feed.children];
  const el = h("div", "msg" + (m.author === mesh.me.nickname ? " mine" : ""),
    h("div", "meta", h("span", "author", m.author), fmt(m.ts)), h("div", "text", m.text));
  el.dataset.ts = m.ts;
  const after = nodes.find((n) => Number(n.dataset.ts) > m.ts); // history can arrive out of order
  feed.insertBefore(el, after || null);
  if (atBottom || !after) feed.scrollTop = feed.scrollHeight;
}

function system(text) {
  const el = h("div", "msg system", text);
  el.dataset.ts = Date.now();
  $("#messages").append(el);
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function save() {
  try { localStorage.setItem(storageKey(mesh.room), JSON.stringify(mesh.messages().slice(-300))); }
  catch (e) { /* storage full or blocked */ }
}

function renderPeers() {
  const members = mesh.members();
  const connecting = [...mesh.peers.values()].filter((p) => !p.info || !p.dc || p.dc.readyState !== "open");
  $("#peers").replaceChildren(
    ...members.map((m) => h("li", m.me ? "me" : "", m.id === mesh.host ? "👑" : "", m.device === "mobile" ? "📱" : "💻",
      m.nickname + (m.me ? " (나)" : ""), m.me ? "" : m.route)),
    ...connecting.map((p) => h("li", "", p.route === "⚠️" ? "⚠️ 연결 실패 (TURN이 필요할 수 있음)" : "⏳ 연결 중…")));
}

$("#send").addEventListener("submit", (e) => {
  e.preventDefault();
  const box = e.target.text, text = box.value.trim();
  if (!text || !mesh) return;
  mesh.say(text);
  box.value = "";
});
$("#send").text.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#send").requestSubmit(); }
});

$("#copy-link").addEventListener("click", async () => {
  const link = `${location.origin}/#${mesh.room}`;
  await navigator.clipboard.writeText(link).catch(() => {});
  toast(`초대 링크를 복사했습니다: ${link}`);
});

$("#leave").addEventListener("click", () => {
  mesh.leave();
  location.hash = "";
  location.reload();
});

// Tell the others right away when this tab closes (pagehide also fires on mobile).
addEventListener("pagehide", () => { if (mesh) mesh.broadcast({ type: "bye" }); });

if (fromHash) toast("닉네임을 확인하고 '코드로 참여'를 누르세요.");
