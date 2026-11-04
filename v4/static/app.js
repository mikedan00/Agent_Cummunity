// Page wiring for the P2P room. User text is always set as text, never as HTML.
"use strict";

const $ = (sel) => document.querySelector(sel);
const DEVICE = matchMedia("(pointer: coarse)").matches || /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)
  ? "mobile" : "pc";
let room = null, identity = null, currentPost = null, tab = "chat";

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v != null && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c);
  return el;
}

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3500);
}

const fmt = (ts) => new Date(ts).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

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

$("#join-default").addEventListener("click", () => enter("lobby", ""));
$("#join-code").addEventListener("click", () => enter($("#room-code").value.trim().toLowerCase(), $("#room-password").value));
$("#create-room").addEventListener("click", async () => {
  const { room: code } = await (await fetch("/api/new-room")).json();
  enter(code, $("#room-password").value);
});

// ---- room ----

async function enter(code, password) {
  const nick = nickname();
  if (!nick) return;
  if (!/^[a-z0-9]{4,16}$/.test(code)) { $("#landing-error").textContent = "방 코드는 영문 소문자와 숫자 4~16자입니다."; return; }
  if (!window.crypto || !crypto.subtle) { $("#landing-error").textContent = "https 주소로 접속해야 합니다."; return; }
  identity = identity || await Identity.load();
  location.hash = code;
  $("#landing").hidden = true;
  $("#room").hidden = $("#tabs").hidden = false;
  $("#room-name").textContent = code;

  room = new Room(code, nick, password, DEVICE, identity);
  room.addEventListener("record", (e) => onRecord(e.detail));
  room.addEventListener("history", () => renderAll());
  room.addEventListener("change", () => { renderPeers(); renderAdmission(); });
  room.addEventListener("admitted", () => { renderAll(); system(`'${code}' 방에 들어왔습니다.`); });
  room.addEventListener("denied", (e) => leaveWith(`입장이 거절되었습니다: ${e.detail.reason}`));
  room.addEventListener("removed", (e) => leaveWith(e.detail.banned ? "호스트가 이 방에서 차단했습니다." : "호스트가 내보냈습니다."));
  room.addEventListener("stuck", () => renderAdmission(true));
  room.addEventListener("joined", (e) => system(`${e.detail.nickname}님이 들어왔습니다.`));
  room.addEventListener("left", (e) => system(`${e.detail.nickname}님이 나갔습니다.`));
  room.addEventListener("host", (e) => system(e.detail.me ? "👑 이제 내가 호스트입니다." : `👑 ${e.detail.nickname}님이 호스트를 이어받았습니다.`));
  room.addEventListener("mod", () => renderAll());
  room.addEventListener("lobby", (e) => {
    $("#status").textContent = e.detail.open ? "로비 연결됨" : `로비 연결 끊김${e.detail.reason ? ` (${e.detail.reason})` : ""} — 대화는 계속되지만 새 입장은 안 됩니다`;
  });
  await room.start();
  renderAll();
}

function leaveWith(text) {
  alert(text);
  room.leave();
  location.hash = "";
  location.reload();
}

function renderAdmission(stuck) {
  const el = $("#admission");
  if (room.admitted) { el.textContent = ""; $("#send").hidden = false; return; }
  $("#send").hidden = true;
  el.textContent = stuck
    ? "호스트에게 연결하지 못했습니다. 방에 아무도 없거나, 네트워크가 직접 연결을 막고 있을 수 있습니다(TURN 필요). 잠시 후 다시 시도해 보세요."
    : "⏳ 호스트의 입장 확인을 기다리는 중…";
}

// ---- records ----

function hostTools(rec) {
  if (!room.isHost() || rec.by === identity.keyId) return [];
  return [h("button", { class: "icon", title: "숨기기 (호스트)", onclick: () => room.moderate("hide", rec.id) }, "🙈")];
}

function itemView(rec) {
  const ai = rec.kind === "agent" || !!rec.as;
  const name = rec.as ? `${rec.as}` : room.nameOf(rec.by);
  return h("div", { class: "msg" + (rec.by === identity.keyId && !ai ? " mine" : "") + (ai ? " ai" : ""), "data-id": rec.id, "data-ts": rec.ts },
    h("div", { class: "meta" }, ai ? h("span", { class: "badge" }, "AI") : null, h("span", { class: "author" }, name),
      rec.as ? h("span", {}, `(${room.nameOf(rec.by)} 경유)`) : null, fmt(rec.ts), ...hostTools(rec)),
    h("div", { class: "text" }, rec.text || ""));
}

function system(text) {
  const el = h("div", { class: "msg system", "data-ts": Date.now() }, text);
  $("#messages").append(el);
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function insertSorted(feed, el) {
  const after = [...feed.children].find((n) => Number(n.dataset.ts) > Number(el.dataset.ts));
  feed.insertBefore(el, after || null);
  if (!after) feed.scrollTop = feed.scrollHeight;
}

function onRecord(rec) {
  if (rec.kind === "chat" && !$(`#messages [data-id="${rec.id}"]`)) insertSorted($("#messages"), itemView(rec));
  if (rec.kind === "post" && tab === "forum") renderPosts();
  if (rec.kind === "comment" && rec.post === currentPost && !$(`#comments [data-id="${rec.id}"]`)) insertSorted($("#comments"), itemView(rec));
  if (rec.kind === "comment" && tab === "forum") renderPosts();
}

function renderAll() {
  const systems = [...$("#messages").querySelectorAll(".system")];
  $("#messages").replaceChildren(...room.chat().map(itemView));
  for (const s of systems) insertSorted($("#messages"), s);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  renderPosts();
  if (currentPost) openPost(currentPost);
  renderPeers();
  renderAdmission();
}

function renderPeers() {
  if (!room) return;
  const members = room.members();
  $("#peers").replaceChildren(...members.map((m) => h("li", { class: m.me ? "me" : "" },
    m.host ? "👑" : "", m.device === "mobile" ? "📱" : "💻",
    m.nickname + (m.me ? " (나)" : "") + (m.admitted ? "" : " (확인 중)"), m.me ? "" : m.route,
    room.isHost() && !m.me && m.admitted ? h("span", { class: "tools" },
      h("button", { class: "icon", title: "내보내기", onclick: () => confirm(`${m.nickname}님을 내보낼까요?`) && room.moderate("kick", m.keyId) }, "🚪"),
      h("button", { class: "icon", title: "차단", onclick: () => confirm(`${m.nickname}님을 이 방에서 차단할까요?`) && room.moderate("ban", m.keyId) }, "⛔")) : null)));
  const connecting = [...room.mesh.peers.values()].filter((p) => !p.info || !room.mesh.isOpen(p));
  for (const p of connecting) $("#peers").append(h("li", {}, p.route === "⚠️" ? "⚠️ 연결 실패 (TURN이 필요할 수 있음)" : "⏳ 연결 중…"));
}

// ---- forum ----

function renderPosts() {
  const posts = room.posts();
  $("#posts").replaceChildren(...(posts.length ? posts.map((p) => h("li", { class: "selectable" + (p.id === currentPost ? " active" : ""), onclick: () => openPost(p.id) },
    h("b", {}, p.title), h("div", { class: "hint" }, `${room.nameOf(p.by)} · ${fmt(p.ts)} · 댓글 ${room.comments(p.id).length}`)))
    : [h("li", { class: "hint" }, "아직 글이 없습니다.")]));
}

function openPost(id) {
  const p = room.posts().find((x) => x.id === id);
  currentPost = p ? id : null;
  if (!p) { $("#post").replaceChildren(h("p", { class: "hint" }, "글을 선택하세요.")); $("#comments").replaceChildren(); $("#comment").hidden = true; return; }
  $("#post").replaceChildren(h("div", { class: "card" }, h("h2", {}, p.title),
    h("div", { class: "hint" }, `${room.nameOf(p.by)} · ${fmt(p.ts)} `, ...hostTools(p)), h("div", { class: "text" }, p.body)));
  $("#comments").replaceChildren(...room.comments(id).map(itemView));
  $("#comment").hidden = !room.admitted;
  renderPosts();
}

$("#new-post").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!room.admitted) return toast("입장 확인 후에 쓸 수 있습니다.");
  const rec = await room.post(e.target.title.value.trim(), e.target.body.value.trim());
  e.target.reset();
  $("#new-post-box").open = false;
  openPost(rec.id);
});

$("#comment").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = e.target.text.value.trim();
  if (!text || !currentPost) return;
  e.target.text.value = "";
  await room.comment(currentPost, text);
});

// ---- chat ----

$("#send").addEventListener("submit", async (e) => {
  e.preventDefault();
  const box = e.target.text, text = box.value.trim();
  if (!text || !room || !room.admitted) return;
  box.value = "";
  await room.say(text);
});
for (const form of ["#send", "#comment"]) {
  $(form).text.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $(form).requestSubmit(); }
  });
}

// ---- tabs, links ----

document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
  tab = b.dataset.tab;
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  for (const t of ["chat", "forum", "ai"]) $(`#view-${t}`).hidden = t !== tab;
  if (tab === "forum") renderPosts();
  if (tab === "ai" && window.renderAi) window.renderAi();
}));

$("#copy-link").addEventListener("click", async () => {
  const link = `${location.origin}/#${room.code}`;
  await navigator.clipboard.writeText(link).catch(() => {});
  toast(`초대 링크를 복사했습니다: ${link}` + (room.config.pwHash ? " (입장 비밀번호는 따로 알려 주세요)" : ""));
});

$("#leave").addEventListener("click", () => { room.leave(); location.hash = ""; location.reload(); });

// Tell the others right away when this tab closes (pagehide also fires on mobile).
addEventListener("pagehide", () => { if (room) room.mesh.broadcast({ type: "bye" }); });

if (fromHash) toast("닉네임(과 비밀번호)을 확인하고 '코드로 참여'를 누르세요.");
