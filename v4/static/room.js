// A room's shared state on top of the mesh: signed records, admission, the host's authority, forum.
//
// Records: chat | post | comment (content, written by admitted members) and
//          config | admit | deny | mod (authority, written by the current host).
// - Every record is signed (crypto.js); names shown are the admitted nickname for the signing key.
// - The host admits newcomers: entry password (as a room-specific hash), unique nickname, not banned.
//   Peers without a link to the host pass join requests on. Nothing but admit/deny is sent to a
//   peer that isn't admitted yet.
// - Live authority records count only when signed by whoever is host in our own view. History sent
//   by members already in the room is trusted (the room trusts its admitted members).
// - Everything is kept in this browser's localStorage; nothing is stored on a server.
"use strict";

const NICK_RE = /^[0-9A-Za-z가-힣_]{2,20}$/;
const RESERVED = new Set(["ai", "admin", "host", "system", "optimist", "critic"]);
const AGENT_NAME_RE = /_(claude|codex|grok|agy|gemini)\d*$/i; // personal agents are named <nickname>_<cli>
const CONTENT = new Set(["chat", "post", "comment", "agent"]);
const AUTHORITY = new Set(["config", "admit", "deny", "mod"]);
const MAX_RECORDS = 1500;
const ADMIT_TIMEOUT_MS = 20000;

class Room extends EventTarget {
  constructor(code, nickname, password, device, identity) {
    super();
    Object.assign(this, { code, nickname, password, device, identity });
    this.records = new Map(); // id -> record (accepted)
    this.pending = []; // content records whose author isn't admitted yet (they can arrive out of order)
    this.roster = new Map(); // keyId -> {nickname, device}
    this.banned = new Set();
    this.hidden = new Set();
    this.config = { pwHash: "" };
    this.admitted = false;
    this.synced = false; // founder, or history received: only then do we know the roster
    this.historyFrom = null;
    this.joinRequests = new Set();
  }

  async start() {
    this.pwHash = this.password ? await sha256(`${this.code}:${this.password}`) : "";
    const mesh = this.mesh = new Mesh(this.code, this.identity.keyId, this.device);
    mesh.helloExtra = () => ({ nickname: this.nickname, pub: this.identity.pub, pwHash: this.pwHash });
    mesh.onMessage = (from, msg) => this.onMessage(from, msg);
    // Until we have the room's history we don't know the roster: any verified member may be the
    // host, and we can't be (so a newcomer never admits people or answers for the room by mistake).
    mesh.eligible = (m) => (m.me ? this.admitted && this.synced : !this.synced || this.isMember(m.keyId));
    mesh.addEventListener("welcome", (e) => { if (e.detail.alone) this.found(); });
    mesh.addEventListener("hello", (e) => this.onHello(e.detail.peerId, e.detail.hello));
    mesh.addEventListener("left", (e) => {
      const r = this.roster.get(e.detail.keyId);
      if (r && this.admitted) this.emit("left", { nickname: r.nickname });
    });
    mesh.addEventListener("host-changed", (e) => {
      if (this.admitted && this.synced && e.detail.previous && !e.detail.previousPresent) {
        this.emit("host", { me: mesh.isHost(), nickname: this.nameOfPeer(mesh.host) });
      }
      this.emit("change");
    });
    mesh.addEventListener("peers", () => this.emit("change"));
    mesh.addEventListener("lobby", (e) => this.emit("lobby", e.detail));
    await this.loadSaved();
    mesh.connect();
    setTimeout(() => { if (!this.admitted) this.emit("stuck"); }, ADMIT_TIMEOUT_MS);
  }

  // ---- helpers ----

  hostKeyId() { const h = this.mesh.hostMember(); return h && h.keyId; }
  isHost() { return this.admitted && this.synced && this.mesh.isHost(); }
  nameOf(keyId) { return (this.roster.get(keyId) || {}).nickname || "?"; }
  nameOfPeer(peerId) {
    const m = this.mesh.members().find((x) => x.id === peerId);
    return m ? this.nameOf(m.keyId) : "?";
  }
  isMember(keyId) { return this.roster.has(keyId) && !this.banned.has(keyId); }
  peerAdmitted(peer) { return !!(peer.info && this.isMember(peer.info.keyId)); }

  // ---- founding & admission ----

  async found() {
    // Nobody else is here: we are the host. (Re)state the room's password and admit ourselves.
    this.synced = true;
    await this.publish({ kind: "config", pwHash: this.pwHash });
    await this.publish({ kind: "admit", key: this.identity.keyId, nickname: this.nickname, device: this.device });
  }

  async onHello(peerId, hello) {
    const req = { peerId, keyId: String(hello.keyId), pub: hello.pub, nickname: String(hello.nickname || ""),
      pwHash: String(hello.pwHash || ""), device: hello.device === "mobile" ? "mobile" : "pc" };
    if ((await keyIdOf(req.pub || {}).catch(() => "")) !== req.keyId) return; // the key must match the id
    if (this.isHost()) return this.admit(req);
    if (this.admitted && !this.isMember(req.keyId)) this.forwardJoin(req, peerId); // let the host decide
    if (this.admitted && this.isMember(req.keyId)) this.sendHistory(peerId); // returning member
  }

  forwardJoin(req, except) {
    const tag = `${req.keyId}:${req.nickname}:${req.pwHash}`;
    if (this.joinRequests.has(tag)) return;
    this.joinRequests.add(tag);
    this.mesh.broadcast({ type: "join-req", req }, except, (id, p) => this.peerAdmitted(p));
  }

  async admit(req) {
    const send = (rec) => this.publish(rec, req.keyId);
    if (this.isMember(req.keyId)) { // a returning member: send its admit again, then the history
      const prior = this.allRecords().filter((r) => r.kind === "admit" && r.key === req.keyId).pop();
      if (prior) this.spread(prior, null, req.keyId);
      this.sendHistory(req.peerId);
      return;
    }
    if (this.banned.has(req.keyId)) return send({ kind: "deny", key: req.keyId, reason: "이 방에서 차단되었습니다." });
    if (this.config.pwHash && req.pwHash !== this.config.pwHash) return send({ kind: "deny", key: req.keyId, reason: "입장 비밀번호가 맞지 않습니다." });
    const lowered = req.nickname.toLowerCase();
    if (!NICK_RE.test(req.nickname) || RESERVED.has(lowered) || AGENT_NAME_RE.test(req.nickname)) {
      return send({ kind: "deny", key: req.keyId, reason: "쓸 수 없는 닉네임입니다." });
    }
    for (const [key, r] of this.roster) {
      if (key !== req.keyId && r.nickname.toLowerCase() === lowered) {
        return send({ kind: "deny", key: req.keyId, reason: "이 방에서 이미 쓰는 닉네임입니다." });
      }
    }
    await send({ kind: "admit", key: req.keyId, nickname: req.nickname, device: req.device });
    this.sendHistory(req.peerId);
  }

  sendHistory(peerId) {
    if (peerId && this.mesh.isOpen(this.mesh.peers.get(peerId))) {
      this.mesh.send(peerId, { type: "history", records: this.allRecords() });
    }
  }

  // ---- records ----

  allRecords() { return [...this.records.values()].sort((a, b) => a.ts - b.ts).slice(-MAX_RECORDS); }

  // Sign, accept locally and send. `alsoTo` (a key id) lets admit/deny reach a peer not admitted yet.
  async publish(fields, alsoTo) {
    const rec = await signRecord(this.identity, fields);
    await this.accept(rec, { live: true });
    this.spread(rec, null, alsoTo);
    return rec;
  }

  spread(rec, except, alsoTo) {
    // admit/deny go to the newcomer, kick/ban to the person removed, even though they aren't members.
    const target = alsoTo || ((rec.kind === "admit" || rec.kind === "deny") ? rec.key
      : rec.kind === "mod" && (rec.action === "kick" || rec.action === "ban") ? rec.target : null);
    this.mesh.broadcast({ type: "rec", rec }, except, (id, p) => this.peerAdmitted(p) || (target && p.info && p.info.keyId === target));
  }

  // Returns true if the record was new and accepted.
  async accept(rec, { live = false, trusted = false } = {}) {
    if (!rec || this.records.has(rec.id) || !(CONTENT.has(rec.kind) || AUTHORITY.has(rec.kind))) return false;
    if (!(await verifyRecord(rec))) return false;
    if (AUTHORITY.has(rec.kind)) {
      if (!trusted && rec.by !== this.hostKeyId() && rec.by !== this.identity.keyId) return false;
      this.records.set(rec.id, rec);
      this.applyAuthority(rec, live);
    } else {
      if (!this.isMember(rec.by)) {
        if (!this.banned.has(rec.by) && this.pending.length < 300) this.pending.push(rec);
        return false;
      }
      if (!this.wellFormed(rec)) return false;
      this.records.set(rec.id, rec);
      if (live) this.emit("record", rec);
    }
    this.save();
    return true;
  }

  wellFormed(rec) {
    const str = (v, max) => typeof v === "string" && v.length <= max;
    if (rec.as !== undefined && !str(rec.as, 40)) return false;
    if (rec.kind === "chat") return str(rec.text, 2000);
    if (rec.kind === "post") return str(rec.title, 100) && str(rec.body, 5000);
    if (rec.kind === "comment") return str(rec.post, 64) && str(rec.text, 2000);
    if (rec.kind === "agent") return str(rec.cli, 10);
    return false;
  }

  applyAuthority(rec, live) {
    if (rec.kind === "config") {
      this.config = { pwHash: String(rec.pwHash || "") };
    } else if (rec.kind === "admit") {
      const isNew = !this.roster.has(rec.key);
      this.roster.set(rec.key, { nickname: String(rec.nickname).slice(0, 20), device: rec.device === "mobile" ? "mobile" : "pc" });
      if (rec.key === this.identity.keyId && !this.admitted) {
        this.admitted = true;
        this.emit("admitted");
        setTimeout(() => this.askHistory(), 1500);
      } else if (live && isNew && this.admitted) {
        this.emit("joined", { nickname: rec.nickname });
      }
      this.retryPending();
    } else if (rec.kind === "deny") {
      if (rec.key === this.identity.keyId && !this.admitted) this.emit("denied", { reason: String(rec.reason || "") });
    } else if (rec.kind === "mod") {
      const action = rec.action, target = String(rec.target || "");
      if (action === "hide") this.hidden.add(target);
      if (action === "ban") this.banned.add(target);
      if (action === "unban") this.banned.delete(target);
      if (action === "ban" || action === "kick") {
        if (target === this.identity.keyId && live) this.emit("removed", { banned: action === "ban" });
        // A moment later, so this record still reaches the person removed before the link closes.
        setTimeout(() => {
          for (const [id, p] of this.mesh.peers) if (p.info && p.info.keyId === target) this.mesh.drop(id, true);
        }, 1000);
      }
      if (live) this.emit("mod", { action, target });
    }
    this.mesh.electHost(); // who may be host depends on the roster and bans
    this.emit("change");
  }

  retryPending() {
    const waiting = this.pending.splice(0);
    for (const rec of waiting) this.accept(rec, { live: true });
  }

  askHistory() {
    if (this.historyFrom) return;
    const hostId = this.mesh.host, peers = [...this.mesh.peers.entries()].filter(([, p]) => this.mesh.isOpen(p) && this.peerAdmitted(p));
    const pick = peers.find(([id]) => id === hostId) || peers[0];
    if (pick) this.mesh.send(pick[0], { type: "need-history" });
  }

  async onMessage(from, msg) {
    const peer = this.mesh.peers.get(from);
    if (!peer || !peer.info) return;
    if (msg.type === "rec") {
      const rec = msg.rec;
      if (await this.accept(rec, { live: true })) this.spread(rec, from);
      else if (rec && (rec.kind === "admit" || rec.kind === "deny") && rec.key === this.identity.keyId && !this.admitted &&
               this.records.has(rec.id) === false && (await verifyRecord(rec))) {
        // our admit/deny signed by a host we don't see yet (no link to it): accept it once
        await this.accept(rec, { live: true, trusted: true });
      }
    } else if (msg.type === "history" && Array.isArray(msg.records)) {
      // From a member already in the room (we trust admitted members, see the header comment).
      const records = msg.records.slice(-MAX_RECORDS).sort((a, b) => a.ts - b.ts);
      for (const r of records) if (AUTHORITY.has(r && r.kind)) await this.accept(r, { trusted: true });
      for (const r of records) if (CONTENT.has(r && r.kind)) await this.accept(r);
      this.historyFrom = from;
      this.synced = true;
      this.mesh.electHost();
      this.emit("history");
    } else if (msg.type === "need-history") {
      if (this.peerAdmitted(peer)) this.sendHistory(from);
    } else if (msg.type === "join-req" && msg.req && this.peerAdmitted(peer)) {
      if (this.isHost()) this.admit({ ...msg.req, peerId: null });
      else this.forwardJoin(msg.req, from);
    }
  }

  // ---- persistence ----

  storageKey() { return `agent-p2p:v2:${this.code}`; }

  async loadSaved() {
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem(this.storageKey()) || "[]"); } catch (e) { /* none */ }
    // Our own admit is left out: each visit is admitted afresh by the current host.
    const mine = (r) => r && r.kind === "admit" && r.key === this.identity.keyId;
    for (const r of saved) if (AUTHORITY.has(r && r.kind) && !mine(r)) await this.accept(r, { trusted: true });
    for (const r of saved) if (CONTENT.has(r && r.kind)) await this.accept(r);
  }

  save() {
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => {
      try { localStorage.setItem(this.storageKey(), JSON.stringify(this.allRecords())); } catch (e) { /* full or blocked */ }
    }, 300);
  }

  // ---- what the page uses ----

  visible(kind) {
    return this.allRecords().filter((r) => r.kind === kind && !this.hidden.has(r.id) && !this.banned.has(r.by));
  }
  chat() { return this.visible("chat"); }
  posts() { return this.visible("post").reverse(); }
  comments(postId) { return this.visible("comment").filter((c) => c.post === postId); }

  members() {
    return this.mesh.members().map((m) => ({ ...m, nickname: m.me ? this.nickname : this.nameOf(m.keyId),
      admitted: m.me ? this.admitted : this.isMember(m.keyId), host: m.id === this.mesh.host }));
  }

  say(text) { return this.publish({ kind: "chat", text: text.slice(0, 2000) }); }

  // Personal agents: "<nickname>_<cli>", announced by the owner ("" switches it off).
  announceAgent(cli) { return this.admitted ? this.publish({ kind: "agent", cli: cli || "" }) : null; }
  activeAgents() {
    const present = new Set(this.mesh.members().map((m) => m.keyId));
    const latest = new Map();
    for (const r of this.allRecords()) if (r.kind === "agent") latest.set(r.by, r);
    return [...latest.values()].filter((r) => r.cli && present.has(r.by) && this.isMember(r.by))
      .map((r) => ({ name: `${this.nameOf(r.by)}_${r.cli}`, owner: this.nameOf(r.by), cli: r.cli }));
  }
  post(title, body) { return this.publish({ kind: "post", title: title.slice(0, 100), body: body.slice(0, 5000) }); }
  comment(postId, text) { return this.publish({ kind: "comment", post: postId, text: text.slice(0, 2000) }); }

  // Host only
  moderate(action, target) {
    if (!this.isHost()) throw new Error("호스트만 할 수 있습니다.");
    return this.publish({ kind: "mod", action, target });
  }

  leave() { this.mesh.leave(); }

  emit(type, detail = {}) { this.dispatchEvent(new CustomEvent(type, { detail })); }
}
