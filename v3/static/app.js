// Agent Community web UI. User text is always inserted as text (never as HTML).
"use strict";

const $ = (sel) => document.querySelector(sel);
const S = { state: null, room: "lobby", tab: "chat", postId: null, ws: null, poll: null, clis: null };

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) el.append(c);
  return el;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    method: options.method || (options.body ? "POST" : "GET"),
    headers: options.body ? { "Content-Type": "application/json" } : {},
    body: options.body ? JSON.stringify(options.body) : undefined,
    credentials: "same-origin",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `오류 (${res.status})`);
  return data;
}

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3500);
}

const fmt = (ts) => new Date(ts * 1000).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit" });
const canWrite = () => S.state && S.state.me && !S.state.me.banned;
const model = () => $("#model").value || null;

// ---- state & layout ----

async function loadState() {
  S.state = await api("/api/state");
  const st = S.state;
  $("#me").replaceChildren(...(st.me
    ? [h("span", {}, `👤 ${st.me.nickname}`, st.me.admin ? " · 호스트" : ""),
       h("button", { class: "secondary", onclick: logout }, "나가기")]
    : [h("button", { onclick: showJoin }, "입장하기")]));
  if (st.me && st.me.banned) $("#me").append(h("span", { class: "error" }, "차단됨"));
  document.querySelector('[data-tab="admin"]').hidden = !st.admin;
  $("#new-room").hidden = $("#send").hidden = $("#new-post-box").hidden = !canWrite();
  $("#comment").hidden = !canWrite() || !S.postId;
  document.querySelector(".readonly").hidden = canWrite();

  $("#rooms").replaceChildren(...st.rooms.map((r) =>
    h("li", { class: "selectable" + (r === S.room ? " active" : ""), onclick: () => openRoom(r) }, `# ${r}`)));
  const select = $("#model"), saved = localStorage.getItem("model");
  select.replaceChildren(...st.models.map((m) => h("option", { value: m }, m.split("/").pop().split(":")[0])));
  if (st.models.includes(saved)) select.value = saved;
  $("#ai-status").textContent = st.ai_ready ? "AI 연결됨" : "호스트가 HF_TOKEN을 설정하지 않아 AI가 답하지 않습니다.";
  $("#forum-agents").textContent = st.agents.map((a) => "@" + a).join(" ");
  $("#personal-agents").replaceChildren(...(st.personal_agents.length
    ? st.personal_agents.map((a) => h("li", {}, `${a.online ? "🟢" : "⚪"} @${a.name}`))
    : [h("li", { class: "hint" }, "아직 없습니다")]));
  $("#usage-box").hidden = !st.usage;
  if (st.usage) {
    const [ai, aiMax] = st.usage.ai_chat, [posts, postsMax] = st.usage.forum_post;
    $("#usage").textContent = `AI ${ai}/${aiMax}회 · 글 ${posts}/${postsMax}개`;
  }
}

function showTab(tab) {
  S.tab = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  for (const t of ["chat", "forum", "ai", "admin"]) $(`#view-${t}`).hidden = t !== tab;
  if (tab === "forum") loadPosts();
  if (tab === "ai") loadAi();
  if (tab === "admin") loadAdmin();
}

// ---- join ----

function showJoin() {
  const params = new URLSearchParams(location.search);
  const form = $("#join-form");
  if (params.get("invite")) form.password.value = params.get("invite");
  $("#password-field").hidden = !S.state.needs_password || !!params.get("invite");
  $("#join-error").textContent = "";
  $("#join").showModal();
}

$("#join-form").addEventListener("submit", async (e) => {
  const form = e.target;
  if (e.submitter && e.submitter.value === "browse") return; // closes the dialog
  e.preventDefault();
  try {
    await api("/api/join", { body: { nickname: form.nickname.value, password: form.password.value } });
    $("#join").close();
    history.replaceState(null, "", "/"); // drop ?invite= from the address bar
    if (form.lobby.checked) S.room = S.state.default_room;
    await loadState();
    await openRoom(S.room);
    toast(`환영합니다, ${S.state.me.nickname}님!`);
  } catch (err) {
    $("#join-error").textContent = err.message;
  }
});

async function logout() {
  await api("/api/logout", { body: {} });
  await loadState();
  await refreshView();
}

// ---- items (messages, posts, comments) ----

function actions(target, item) {
  const out = [];
  if (canWrite() && item.user_id !== S.state.me.id) {
    out.push(h("button", { class: "icon", title: "신고", onclick: () => report(target, item.id) }, "🚩"));
  }
  if (S.state.admin) {
    out.push(h("button", { class: "icon", title: "숨기기 (관리자)", onclick: () => hide(target, item.id) }, "🙈"));
  }
  return out;
}

function itemView(target, item) {
  return h("div", { class: "msg" + (item.is_ai ? " ai" : ""), "data-id": item.id },
    h("div", { class: "meta" }, item.is_ai ? h("span", { class: "badge" }, "AI") : null,
      h("span", { class: "author" }, item.author), h("span", {}, fmt(item.ts)), ...actions(target, item)),
    h("div", { class: "text" }, item.text));
}

async function report(target, id) {
  try {
    const r = await api("/api/report", { body: { target, target_id: id } });
    toast(r.reported ? "신고했습니다. 호스트가 확인합니다." : "이미 신고했습니다.");
  } catch (err) { toast(err.message); }
}

async function hide(target, id) {
  try { await api("/api/admin/hide", { body: { target, target_id: id } }); await refreshView(); }
  catch (err) { toast(err.message); }
}

// ---- chat ----

async function openRoom(room) {
  S.room = room;
  $("#room-title").textContent = `# ${room}`;
  document.querySelectorAll("#rooms li").forEach((li) => li.classList.toggle("active", li.textContent === `# ${room}`));
  const msgs = await api(`/api/rooms/${encodeURIComponent(room)}/messages`);
  $("#messages").replaceChildren(...msgs.map((m) => itemView("message", m)));
  scrollDown($("#messages"));
}

function scrollDown(el) { el.scrollTop = el.scrollHeight; }

$("#send").addEventListener("submit", async (e) => {
  e.preventDefault();
  const box = e.target.text, text = box.value.trim();
  if (!text) return;
  box.value = "";
  const button = e.target.querySelector("button");
  button.disabled = true;
  try {
    await api(`/api/rooms/${encodeURIComponent(S.room)}/messages`, { body: { text, model: model() } });
    if (!S.ws) await openRoom(S.room);
    await loadState();
  } catch (err) { toast(err.message); box.value = text; }
  button.disabled = false;
});

for (const form of ["#send", "#comment"]) {
  $(form).text.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $(form).requestSubmit(); }
  });
}

$("#new-room").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const { room } = await api("/api/rooms", { body: { name: e.target.name.value } });
    e.target.reset();
    S.room = room;
    await loadState();
    await openRoom(room);
  } catch (err) { toast(err.message); }
});

// ---- forum ----

async function loadPosts() {
  const posts = await api("/api/posts");
  $("#posts").replaceChildren(...(posts.length ? posts.map((p) =>
    h("li", { class: "selectable" + (p.id === S.postId ? " active" : ""), onclick: () => openPost(p.id) },
      h("b", {}, `#${p.id} ${p.title}`), h("div", { class: "hint" }, `${p.author} · 댓글 ${p.comments}`)))
    : [h("li", { class: "hint" }, "아직 글이 없습니다. 첫 아이디어를 올려보세요.")]));
}

async function openPost(id) {
  S.postId = id;
  let data;
  try { data = await api(`/api/posts/${id}`); }
  catch (err) { S.postId = null; $("#post").replaceChildren(h("p", { class: "hint" }, err.message)); return; }
  const p = data.post;
  $("#post").replaceChildren(h("div", { class: "card" },
    h("h2", {}, p.title), h("div", { class: "hint" }, `${p.author} · ${fmt(p.ts)} `, ...actions("post", p)),
    h("div", { class: "post-body" }, p.body)));
  $("#comments").replaceChildren(...data.comments.map((c) => itemView("comment", c)));
  $("#comment").hidden = !canWrite();
  loadPosts();
}

$("#new-post").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const post = await api("/api/posts", { body: { title: e.target.title.value, body: e.target.body.value, model: model() } });
    e.target.reset();
    $("#new-post-box").open = false;
    await openPost(post.id);
    await loadState();
  } catch (err) { toast(err.message); }
});

$("#comment").addEventListener("submit", async (e) => {
  e.preventDefault();
  const box = e.target.text, text = box.value.trim();
  if (!text || !S.postId) return;
  box.value = "";
  try {
    await api(`/api/posts/${S.postId}/comments`, { body: { text, model: model() } });
    if (!S.ws) await openPost(S.postId);
  } catch (err) { toast(err.message); box.value = text; }
});

// ---- my AI ----

async function loadAi() {
  if (!S.clis) {
    S.clis = await api("/api/clis");
    $("#cli").replaceChildren(...Object.entries(S.clis).map(([k, v]) => h("option", { value: k }, v.label)));
  }
  renderCliGuide();
  $("#telegram-box").hidden = !(canWrite() && S.state.telegram_bot);
  if (!canWrite()) {
    $("#agent-create").replaceChildren(h("p", { class: "hint" }, "입장한 뒤에 에이전트를 만들 수 있습니다."));
    $("#my-agents").replaceChildren();
    return;
  }
  const cli = $("#cli").value;
  $("#agent-create").replaceChildren(h("button", { onclick: createAgent }, `@${S.state.me.nickname}_${cli} 에이전트 만들기`));
  const mine = await api("/api/my/agents");
  $("#my-agents").replaceChildren(...mine.map((a) => h("li", {}, `${a.online ? "🟢" : "⚪"} @${a.name} `,
    h("button", { class: "link", onclick: () => revokeAgent(a.name), title: "토큰이 유출됐을 때: 폐기하면 그 토큰으로 접속할 수 없습니다." }, "폐기"))));
}

function renderCliGuide() {
  const spec = S.clis[$("#cli").value];
  const win = spec.install.windows;
  $("#cli-guide").replaceChildren(
    h("p", { class: "hint" }, `필요한 계정: ${spec.account} · `, h("a", { href: spec.docs, target: "_blank", rel: "noopener" }, "공식 안내")),
    spec.note ? h("p", { class: "error" }, spec.note) : null,
    spec.safe ? null : h("p", { class: "hint" }, "이 CLI는 도구를 완전히 끌 수 없어서, 커넥터가 답변을 보내기 전에 노트북에서 확인을 받습니다."),
    h("h3", {}, "1. CLI 설치 (이미 있으면 건너뛰기)"),
    h("div", { class: "hint" }, "macOS / Linux"), h("pre", { class: "command" }, spec.install.posix),
    h("div", { class: "hint" }, "Windows (PowerShell)"), h("pre", { class: "command" }, win || "공식 안내 페이지를 확인하세요."),
    h("h3", {}, "2. 로그인"), spec.login ? h("pre", { class: "command" }, spec.login.join(" ")) : null,
    h("p", { class: "hint" }, spec.login_hint));
}

$("#cli").addEventListener("change", loadAi);

async function createAgent() {
  try {
    const r = await api("/api/my/agents", { body: { cli: $("#cli").value } });
    $("#agent-create").replaceChildren(
      h("p", {}, `✅ @${r.name}를 만들었습니다. 노트북 터미널에서 실행하세요:`),
      h("pre", { class: "command" }, r.command),
      h("p", { class: "error" }, "토큰은 지금 한 번만 보입니다. 다른 사람에게 보여주지 마세요."));
    const mine = await api("/api/my/agents");
    $("#my-agents").replaceChildren(...mine.map((a) => h("li", {}, `${a.online ? "🟢" : "⚪"} @${a.name}`)));
    await loadState();
  } catch (err) { toast(err.message); }
}

async function revokeAgent(name) {
  if (!confirm(`@${name}를 폐기할까요? 이 토큰으로는 더 이상 접속할 수 없습니다.`)) return;
  try { await api(`/api/my/agents/${encodeURIComponent(name)}`, { method: "DELETE" }); await loadAi(); await loadState(); }
  catch (err) { toast(err.message); }
}

$("#telegram-code").addEventListener("click", async () => {
  try {
    const r = await api("/api/my/telegram-code", { body: {} });
    $("#telegram-result").textContent = `봇 @${r.bot}에게 /link ${r.code} 를 보내세요. 10분 동안 유효합니다.`;
  } catch (err) { toast(err.message); }
});

// ---- admin ----

async function loadAdmin() {
  if (!S.state.admin) return;
  $("#invite-url").value = S.state.invite_url;
  $("#qr").src = `/api/admin/qr.svg?u=${encodeURIComponent(S.state.invite_url)}`;
  $("#invite-note").textContent = S.state.invite_url.includes("trycloudflare.com")
    ? "인터넷 공개 주소입니다. 앱을 다시 시작하면 주소가 바뀝니다."
    : "공개 터널이 아직 준비되지 않았거나 꺼져 있습니다. 이 주소는 같은 네트워크에서만 열립니다.";
  const s = await api("/api/admin/settings"), f = $("#settings");
  f.password.value = s.password;
  f.hf_token.value = ""; f.hf_token.placeholder = s.hf_token ? "설정됨 (바꾸려면 새로 입력)" : "hf_...";
  f.tavily_key.value = ""; f.tavily_key.placeholder = s.tavily_key ? "설정됨" : "선택";
  f.models.value = s.models.join(", ");
  for (const k of ["ai_per_day", "posts_per_day", "agent_calls_per_hour", "messages_per_min"]) f[k].value = s.limits[k];
  f.global_ai_per_day.value = s.global_ai_per_day;
  f.host_agent_cli.value = s.host_agent_cli;
  $("#host-agent-status").textContent = `상태: ${S.state.host_agent}. 앱 안에서는 도구를 모두 끈 Claude Code만 실행합니다. 호스트 구독 사용량이 나가며 시간당 호출 한도가 적용됩니다.`;
  await loadReports();
}

$("#copy-invite").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("#invite-url").value).catch(() => {});
  toast("초대 링크를 복사했습니다.");
});

$("#new-password").addEventListener("click", () => saveSettings({ password: "new" }));

$("#settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  if (!f.password.value && !confirm("비밀번호를 끄면 링크를 아는 누구나 들어올 수 있습니다. 계속할까요?")) return;
  const changes = {
    password: f.password.value.trim(),
    models: f.models.value.split(",").map((m) => m.trim()).filter(Boolean),
    limits: Object.fromEntries(["ai_per_day", "posts_per_day", "agent_calls_per_hour", "messages_per_min"]
      .map((k) => [k, Number(f[k].value)])),
    global_ai_per_day: Number(f.global_ai_per_day.value),
    host_agent_cli: f.host_agent_cli.value,
  };
  if (f.hf_token.value) changes.hf_token = f.hf_token.value.trim();
  if (f.tavily_key.value) changes.tavily_key = f.tavily_key.value.trim();
  await saveSettings(changes);
});

async function saveSettings(changes) {
  try {
    await api("/api/admin/settings", { method: "PUT", body: changes });
    $("#settings-result").textContent = "저장했습니다.";
    await loadState();
    await loadAdmin();
  } catch (err) { $("#settings-result").textContent = err.message; }
}

async function loadReports() {
  const reports = await api("/api/admin/reports");
  const act = (path, r) => async () => {
    try { await api(path, { body: { target: r.target, target_id: r.target_id } }); await loadReports(); await refreshView(); }
    catch (err) { toast(err.message); }
  };
  $("#reports").replaceChildren(...(reports.length ? reports.map((r) => h("li", { class: "card" },
    h("b", {}, `${r.target} #${r.target_id} · ${r.author} · 신고 ${r.count}건`),
    h("div", { class: "text" }, r.text.slice(0, 300)),
    h("div", { class: "row" },
      h("button", { onclick: act("/api/admin/hide", r) }, "숨기기"),
      h("button", { class: "secondary", onclick: act("/api/admin/dismiss", r) }, "무시"),
      r.author_id ? h("button", { class: "secondary", onclick: act("/api/admin/ban", r) }, "작성자 차단") : null)))
    : [h("li", { class: "hint" }, "처리할 신고가 없습니다.")]));
}

// ---- realtime ----

async function refreshView() {
  if (S.tab === "chat") await openRoom(S.room);
  if (S.tab === "forum") { await loadPosts(); if (S.postId) await openPost(S.postId); }
}

function onRecord(r) {
  if (r.type === "message" && r.room === S.room) {
    const feed = $("#messages"), atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
    if (!feed.querySelector(`[data-id="${r.id}"]`)) feed.append(itemView("message", r));
    if (atBottom) scrollDown(feed);
  } else if (r.type === "comment" && r.post_id === S.postId) {
    if (!$("#comments").querySelector(`[data-id="${r.id}"]`)) $("#comments").append(itemView("comment", r));
  } else if (r.type === "post" && S.tab === "forum") {
    loadPosts();
  } else if (r.type === "room" || r.type === "personal_agent") {
    loadState();
  } else if (r.type === "hidden") {
    refreshView();
  }
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { S.ws = ws; clearInterval(S.poll); S.poll = null; };
  ws.onmessage = (e) => onRecord(JSON.parse(e.data));
  ws.onclose = () => {
    S.ws = null;
    if (!S.poll) S.poll = setInterval(refreshView, 5000); // fallback while disconnected
    setTimeout(connect, 3000);
  };
}

// ---- start ----

document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
document.querySelector('[data-action="show-join"]').addEventListener("click", showJoin);

(async () => {
  await loadState();
  await openRoom(S.room);
  connect();
  setInterval(loadState, 30000); // online status and usage
  if (!S.state.me) showJoin();
})();
