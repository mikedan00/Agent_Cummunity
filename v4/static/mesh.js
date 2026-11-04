// Full-mesh WebRTC transport. The lobby only relays signals; everything else goes browser to browser.
//
// - The newcomer offers a connection to everyone already in the room (no offer collisions).
// - Every ping carries the sender's direct links, so everyone also knows members it has no
//   direct link to. Missing links are retried through the lobby (the newer peer offers).
// - The host is computed, not negotiated: among eligible members, PCs before phones, then
//   the earliest join order. The join order comes from a lobby-signed ticket, so nobody can
//   claim to have joined first. When the host leaves, everyone picks the same next one.
// - A peer silent for PEER_TIMEOUT_MS is gone (a closed tab can take WebRTC tens of seconds
//   to notice). Leaving sends "bye" so others react at once.
//
// Join orders are only trusted with a valid lobby ticket (from a hello, or passed along in pings);
// unverified members can't be host.
//
// Hooks set by the room: helloExtra() adds fields to our hello; onMessage(peerId, msg) gets
// every non-transport message; eligible(member) decides who may be host.
"use strict";

const PING_MS = 4000, PEER_TIMEOUT_MS = 12000, LEFT_CHECK_MS = 3000;
const CONNECT_TIMEOUT_MS = 20000, RETRY_MS = 30000;

class Mesh extends EventTarget {
  constructor(room, keyId, device) {
    super();
    this.room = room;
    this.keyId = keyId;
    this.me = { id: null, seq: 0, device, ticket: null };
    this.lobbyKey = null;
    this.peers = new Map(); // id -> {pc, dc, info, route, pending, lastSeen, created}: direct links
    this.known = new Map(); // id -> {keyId, seq, device, seen}: members heard of through others
    this.gone = new Set(); // ids that left for good (bye / timeout / kicked)
    this.attempts = new Map(); // id -> last time we tried to (re)connect
    this.host = null;
    this.iceServers = [];
    this.helloExtra = () => ({});
    this.onMessage = () => {};
    this.eligible = () => true;
    this.heartbeat = setInterval(() => this.tick(), PING_MS);
  }

  // ---- lobby ----

  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.lobby = new WebSocket(`${proto}://${location.host}/ws/${this.room}?key=${encodeURIComponent(this.keyId)}`);
    this.lobby.onmessage = (e) => this.onLobby(JSON.parse(e.data));
    this.lobby.onclose = (e) => this.emit("lobby", { open: false, reason: e.reason });
    this.lobby.onopen = () => this.emit("lobby", { open: true });
  }

  onLobby(msg) {
    if (msg.type === "welcome") {
      Object.assign(this.me, { id: msg.id, seq: msg.seq, ticket: msg.ticket });
      this.lobbyKey = msg.lobbyKey;
      this.iceServers = msg.iceServers;
      this.emit("welcome", { alone: msg.peers.length === 0 });
      this.electHost();
      for (const id of msg.peers) this.open(id, true);
    } else if (msg.type === "signal") {
      if (!this.gone.has(msg.from)) this.onSignal(msg.from, msg.data);
    } else if (msg.type === "peer-left") {
      // Its lobby connection closed. Usually the tab is gone, but the data channel may still be fine
      // (only its lobby link dropped), so ask it directly before dropping.
      const p = this.peers.get(msg.id);
      if (!this.isOpen(p)) { this.drop(msg.id, true); return; }
      const asked = Date.now();
      this.send(msg.id, { type: "ping" });
      setTimeout(() => { const q = this.peers.get(msg.id); if (q && q.lastSeen < asked) this.drop(msg.id, true); }, LEFT_CHECK_MS);
    }
  }

  signal(to, data) {
    if (this.lobby && this.lobby.readyState === WebSocket.OPEN) this.lobby.send(JSON.stringify({ type: "signal", to, data }));
  }

  // ---- peer connections ----

  isOpen(p) { return !!(p && p.dc && p.dc.readyState === "open"); }

  open(id, initiator) {
    this.attempts.set(id, Date.now());
    const pc = new RTCPeerConnection({ iceServers: this.iceServers });
    const peer = { pc, dc: null, info: null, route: "⏳", pending: [], lastSeen: Date.now(), created: Date.now() };
    this.peers.set(id, peer);
    pc.onicecandidate = (e) => { if (e.candidate) this.signal(id, { candidate: e.candidate }); };
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === "failed") { peer.route = "⚠️"; this.emit("peers"); }
    };
    if (initiator) {
      this.wire(id, pc.createDataChannel("mesh"));
      pc.createOffer().then((o) => pc.setLocalDescription(o)).then(() => this.signal(id, { sdp: pc.localDescription }));
    } else {
      pc.ondatachannel = (e) => this.wire(id, e.channel);
    }
    this.emit("peers");
    return peer;
  }

  async onSignal(from, data) {
    let peer = this.peers.get(from);
    if (data.sdp && data.sdp.type === "offer" && peer) {
      // Both sides offered at once: the older member keeps its own offer and ignores this one.
      if (peer.pc.signalingState !== "stable" && this.me.seq < this.seqOf(from)) return;
      this.discard(from); // otherwise a new offer replaces whatever link we had
      peer = null;
    }
    if (!peer) peer = this.open(from, false);
    const pc = peer.pc;
    try {
      if (data.sdp) {
        await pc.setRemoteDescription(data.sdp);
        for (const c of peer.pending.splice(0)) await pc.addIceCandidate(c).catch(() => {});
        if (data.sdp.type === "offer") {
          await pc.setLocalDescription(await pc.createAnswer());
          this.signal(from, { sdp: pc.localDescription });
        }
      } else if (data.candidate) {
        if (pc.remoteDescription) await pc.addIceCandidate(data.candidate).catch(() => {});
        else peer.pending.push(data.candidate);
      }
    } catch (e) { /* a stale or crossed signal: the retry loop will try again */ }
  }

  wire(id, dc) {
    const peer = this.peers.get(id);
    peer.dc = dc;
    dc.onopen = () => {
      this.send(id, { type: "hello", seq: this.me.seq, device: this.me.device, ticket: this.me.ticket,
        keyId: this.keyId, ...this.helloExtra() });
      this.detectRoute(id);
    };
    dc.onmessage = (e) => { let m; try { m = JSON.parse(e.data); } catch (err) { return; } this.onData(id, m); };
    dc.onclose = () => { if (this.peers.get(id) === peer) this.drop(id, false); };
  }

  async detectRoute(id) {
    const peer = this.peers.get(id);
    if (!peer) return;
    const stats = await peer.pc.getStats();
    let pair = null;
    stats.forEach((s) => { if (s.type === "candidate-pair" && s.nominated && s.state === "succeeded") pair = s; });
    const types = [pair && stats.get(pair.localCandidateId), pair && stats.get(pair.remoteCandidateId)]
      .map((c) => c && c.candidateType);
    peer.route = types.includes("relay") ? "🔁" : types.includes("srflx") || types.includes("prflx") ? "🌐" : "🏠";
    this.emit("peers");
  }

  discard(id) { // close a direct link without any announcement
    const peer = this.peers.get(id);
    if (!peer) return;
    this.peers.delete(id);
    try { peer.pc.close(); } catch (e) { /* already closed */ }
  }

  drop(id, gone) {
    const peer = this.peers.get(id);
    const info = (peer && peer.info) || this.known.get(id);
    this.discard(id);
    if (gone) {
      this.gone.add(id);
      this.known.delete(id);
      if (info) this.emit("left", { peerId: id, keyId: info.keyId });
    } else if (peer && peer.info) {
      this.known.set(id, { ...peer.info, seen: Date.now() }); // still in the room as far as we know
      this.attempts.delete(id); // a working link broke: retry at the next tick
    }
    this.electHost();
    this.emit("peers");
  }

  tick() {
    const now = Date.now();
    const links = this.members().filter((m) => (m.direct || m.me) && Number.isFinite(m.seq))
      .map(({ id, keyId, seq, device, ticket }) => ({ id, keyId, seq, device, ticket }));
    for (const [id, p] of [...this.peers]) {
      if (this.isOpen(p)) {
        if (now - p.lastSeen > PEER_TIMEOUT_MS) this.drop(id, true);
        else this.send(id, { type: "ping", links });
      } else if (now - p.created > CONNECT_TIMEOUT_MS) {
        this.discard(id); // never connected; relaying through others still works
      }
    }
    for (const [id, k] of [...this.known]) {
      if (this.peers.has(id)) continue;
      if (now - k.seen > PEER_TIMEOUT_MS) { this.known.delete(id); continue; }
      // Try a direct link again; the newer member offers.
      if (Number.isFinite(k.seq) && this.me.seq > k.seq && now - (this.attempts.get(id) || 0) > RETRY_MS) this.open(id, true);
    }
    this.electHost();
    this.emit("peers");
  }

  send(id, obj) {
    const peer = this.peers.get(id);
    if (this.isOpen(peer)) peer.dc.send(JSON.stringify(obj));
  }

  // filter(peerId, peer) -> bool decides who receives it (e.g. only admitted members)
  broadcast(obj, except, filter) {
    for (const [id, p] of this.peers) if (id !== except && (!filter || filter(id, p))) this.send(id, obj);
  }

  // ---- data ----

  async onData(from, msg) {
    const peer = this.peers.get(from);
    if (!peer) return;
    peer.lastSeen = Date.now();
    if (msg.type === "ping") {
      for (const m of Array.isArray(msg.links) ? msg.links.slice(0, 50) : []) {
        if (!m.id || m.id === this.me.id || this.isOpen(this.peers.get(m.id)) || this.gone.has(m.id)) continue;
        const prev = this.known.get(m.id);
        if (prev && prev.keyId === m.keyId) { prev.seen = Date.now(); continue; }
        const entry = { keyId: String(m.keyId || ""), seq: Infinity, device: m.device === "mobile" ? "mobile" : "pc",
          ticket: m.ticket, seen: Date.now() };
        this.known.set(m.id, entry);
        this.checkTicket(m.id, m.seq, entry.keyId, m.ticket).then((seq) => { entry.seq = seq; this.electHost(); this.emit("peers"); });
      }
      this.send(from, { type: "pong" });
    } else if (msg.type === "pong") {
      // lastSeen already updated
    } else if (msg.type === "bye") {
      this.drop(from, true);
    } else if (msg.type === "hello") {
      const seq = await this.checkTicket(from, msg.seq, String(msg.keyId), msg.ticket);
      if (!this.peers.has(from)) return;
      peer.info = { keyId: String(msg.keyId), seq, device: msg.device === "mobile" ? "mobile" : "pc", ticket: msg.ticket };
      this.known.delete(from);
      this.electHost();
      this.emit("hello", { peerId: from, hello: msg });
      this.emit("peers");
    } else {
      this.onMessage(from, msg);
    }
  }

  // ---- members & host ----

  async checkTicket(peerId, seq, keyId, ticket) {
    // The join order counts only if the lobby signed it for this peer id and key.
    return this.lobbyKey && await verifyTicket(this.lobbyKey, this.room, peerId, Number(seq), keyId, ticket)
      ? Number(seq) : Infinity;
  }

  seqOf(id) {
    const p = this.peers.get(id);
    return (p && p.info && p.info.seq) || (this.known.get(id) || {}).seq || Infinity;
  }

  members() {
    const direct = [...this.peers.entries()].filter(([, p]) => p.info && this.isOpen(p))
      .map(([id, p]) => ({ id, ...p.info, route: p.route, direct: true }));
    const indirect = [...this.known.entries()].filter(([id]) => !direct.some((d) => d.id === id))
      .map(([id, k]) => ({ id, keyId: k.keyId, seq: k.seq, device: k.device, route: "↔️", direct: false }));
    const me = { id: this.me.id, keyId: this.keyId, seq: this.me.seq, device: this.me.device, ticket: this.me.ticket, me: true };
    return [me, ...direct, ...indirect];
  }

  electHost() {
    const order = (m) => [m.device === "mobile" ? 1 : 0, m.seq];
    const members = this.members().filter((m) => m.seq && Number.isFinite(m.seq) && this.eligible(m));
    const best = [...members].sort((a, b) => {
      const [x, y] = [order(a), order(b)];
      return x[0] - y[0] || x[1] - y[1];
    })[0];
    if (best && best.id !== this.host) {
      const previous = this.host;
      this.host = best.id;
      this.emit("host-changed", { previous, previousPresent: members.some((m) => m.id === previous) });
    }
  }

  hostMember() { return this.members().find((m) => m.id === this.host); }
  isHost() { return this.host === this.me.id; }

  leave() {
    clearInterval(this.heartbeat);
    this.broadcast({ type: "bye" });
    for (const id of [...this.peers.keys()]) this.discard(id);
    if (this.lobby) this.lobby.close();
  }

  emit(type, detail = {}) { this.dispatchEvent(new CustomEvent(type, { detail })); }
}
