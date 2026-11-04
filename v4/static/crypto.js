// Identity keys and signatures (WebCrypto ECDSA P-256 / SHA-256).
//
// Every browser has its own key pair, kept in localStorage. Its key id (a hash of the public key)
// is the person's identity in a room; every record it writes is signed, so peers that relay a
// record can't change it or write in someone else's name.
"use strict";

const ECDSA = { name: "ECDSA", namedCurve: "P-256" };
const SIGN = { name: "ECDSA", hash: "SHA-256" };
const enc = new TextEncoder();

function b64u(buf) {
  let s = "";
  for (const b of new Uint8Array(buf)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64u(s) {
  const t = s.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(t + "===".slice((t.length + 3) % 4)), (c) => c.charCodeAt(0));
}

// JSON with sorted keys, so signer and verifier serialize the same object identically.
function canonical(v) {
  if (Array.isArray(v)) return "[" + v.map(canonical).join(",") + "]";
  if (v && typeof v === "object") {
    return "{" + Object.keys(v).sort().filter((k) => v[k] !== undefined)
      .map((k) => JSON.stringify(k) + ":" + canonical(v[k])).join(",") + "}";
  }
  return JSON.stringify(v);
}

async function sha256(text) {
  return b64u(await crypto.subtle.digest("SHA-256", enc.encode(text)));
}

const publicPart = (jwk) => ({ crv: jwk.crv, kty: jwk.kty, x: jwk.x, y: jwk.y });
const keyIdOf = async (pub) => (await sha256(canonical(publicPart(pub)))).slice(0, 32);

const verifyKeys = new Map(); // "x.y" -> CryptoKey
async function verify(pub, text, sig) {
  try {
    const cacheKey = pub.x + "." + pub.y;
    let key = verifyKeys.get(cacheKey);
    if (!key) {
      key = await crypto.subtle.importKey("jwk", { ...publicPart(pub), ext: true }, ECDSA, false, ["verify"]);
      verifyKeys.set(cacheKey, key);
    }
    return await crypto.subtle.verify(SIGN, key, unb64u(sig), enc.encode(text));
  } catch (e) {
    return false;
  }
}

class Identity {
  static async load() {
    const id = new Identity();
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem("agent-p2p:identity") || "null"); } catch (e) { /* none */ }
    if (saved) {
      id.privateKey = await crypto.subtle.importKey("jwk", saved.priv, ECDSA, false, ["sign"]);
      id.pub = saved.pub;
    } else {
      const pair = await crypto.subtle.generateKey(ECDSA, true, ["sign", "verify"]);
      const priv = await crypto.subtle.exportKey("jwk", pair.privateKey);
      id.pub = publicPart(await crypto.subtle.exportKey("jwk", pair.publicKey));
      id.privateKey = pair.privateKey;
      try { localStorage.setItem("agent-p2p:identity", JSON.stringify({ priv, pub: id.pub })); } catch (e) { /* private mode */ }
    }
    id.keyId = await keyIdOf(id.pub);
    return id;
  }

  async sign(text) {
    return b64u(await crypto.subtle.sign(SIGN, this.privateKey, enc.encode(text)));
  }
}

// A signed record: {kind, id, ts, by, pub, ...fields, sig}
async function signRecord(identity, fields) {
  const rec = { ...fields, id: fields.id || crypto.randomUUID(), ts: fields.ts || Date.now(), by: identity.keyId, pub: identity.pub };
  rec.sig = await identity.sign(canonical(rec));
  return rec;
}

async function verifyRecord(rec) {
  if (!rec || typeof rec !== "object" || typeof rec.sig !== "string" || !rec.pub || typeof rec.by !== "string") return false;
  if ((await keyIdOf(rec.pub)) !== rec.by) return false;
  const { sig, ...body } = rec;
  return verify(rec.pub, canonical(body), sig);
}

// The lobby's join ticket: its signature over "room|peer id|join order|key id".
const verifyTicket = (lobbyKey, room, peerId, seq, keyId, ticket) =>
  typeof ticket === "string" && verify(lobbyKey, `${room}|${peerId}|${seq}|${keyId}`, ticket);
