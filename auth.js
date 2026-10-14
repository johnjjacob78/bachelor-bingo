// Passkey (WebAuthn) + signed-cookie sessions, no dependencies.

// ---------- encoding ----------
export const b64u = {
  enc(buf) {
    const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    let s = "";
    for (const b of bytes) s += String.fromCharCode(b);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  },
  dec(str) {
    const s = String(str).replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(s + "=".repeat((4 - (s.length % 4)) % 4));
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  },
};

const te = new TextEncoder();
const sha256 = async (data) => new Uint8Array(await crypto.subtle.digest("SHA-256", data));
const eq = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

// ---------- minimal CBOR decoder (what WebAuthn needs) ----------
function cborDecode(bytes, start = 0) {
  let pos = start;
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  function readLen(info) {
    if (info < 24) return info;
    if (info === 24) return bytes[pos++];
    if (info === 25) { const v = dv.getUint16(pos); pos += 2; return v; }
    if (info === 26) { const v = dv.getUint32(pos); pos += 4; return v; }
    if (info === 27) { const v = Number(dv.getBigUint64(pos)); pos += 8; return v; }
    throw new Error("cbor: unsupported length");
  }
  function item() {
    const b = bytes[pos++];
    const major = b >> 5, info = b & 31;
    switch (major) {
      case 0: return readLen(info);
      case 1: return -1 - readLen(info);
      case 2: { const n = readLen(info); const v = bytes.slice(pos, pos + n); pos += n; return v; }
      case 3: { const n = readLen(info); const v = new TextDecoder().decode(bytes.slice(pos, pos + n)); pos += n; return v; }
      case 4: { const n = readLen(info); const a = []; for (let i = 0; i < n; i++) a.push(item()); return a; }
      case 5: { const n = readLen(info); const m = new Map(); for (let i = 0; i < n; i++) { const k = item(); m.set(k, item()); } return m; }
      case 7: if (info === 20) return false; if (info === 21) return true; if (info === 22) return null; throw new Error("cbor: simple");
      default: throw new Error("cbor: tag");
    }
  }
  const value = item();
  return { value, end: pos };
}

// ---------- authenticator data ----------
function parseAuthData(ad) {
  const rpIdHash = ad.slice(0, 32);
  const flags = ad[32];
  const counter = new DataView(ad.buffer, ad.byteOffset + 33, 4).getUint32(0);
  let cred = null;
  if (flags & 0x40) {
    const idLen = (ad[53] << 8) | ad[54];
    const credId = ad.slice(55, 55 + idLen);
    const { value: cose } = cborDecode(ad, 55 + idLen);
    cred = { credId, cose };
  }
  return { rpIdHash, flags, counter, cred, up: !!(flags & 1), uv: !!(flags & 4) };
}

function coseToJwk(cose) {
  const kty = cose.get(1), alg = cose.get(3);
  if (kty === 2 && alg === -7) {
    return { alg: -7, jwk: { kty: "EC", crv: "P-256", x: b64u.enc(cose.get(-2)), y: b64u.enc(cose.get(-3)) } };
  }
  if (kty === 3 && alg === -257) {
    return { alg: -257, jwk: { kty: "RSA", n: b64u.enc(cose.get(-1)), e: b64u.enc(cose.get(-2)) } };
  }
  throw new Error("Unsupported passkey algorithm");
}

async function importKey(alg, jwk) {
  if (alg === -7) return crypto.subtle.importKey("jwk", jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
  return crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
}

// DER ECDSA signature -> raw r||s (64 bytes)
function derToRaw(der) {
  let p = 2;
  if (der[p++] !== 2) throw new Error("bad sig");
  let rl = der[p++]; let r = der.slice(p, p + rl); p += rl;
  if (der[p++] !== 2) throw new Error("bad sig");
  let sl = der[p++]; let s = der.slice(p, p + sl);
  const norm = (x) => { while (x.length > 32 && x[0] === 0) x = x.slice(1); const o = new Uint8Array(32); o.set(x, 32 - x.length); return o; };
  const out = new Uint8Array(64); out.set(norm(r), 0); out.set(norm(s), 32); return out;
}

async function verifySig(alg, jwk, data, sig) {
  const key = await importKey(alg, jwk);
  if (alg === -7) return crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, derToRaw(sig), data);
  return crypto.subtle.verify({ name: "RSASSA-PKCS1-v1_5" }, key, sig, data);
}

function parseClientData(cdjBytes, expectType, expectChallenge, expectOrigin) {
  const cd = JSON.parse(new TextDecoder().decode(cdjBytes));
  if (cd.type !== expectType) throw new Error("Wrong ceremony type");
  if (cd.challenge !== expectChallenge) throw new Error("Challenge mismatch");
  if (cd.origin !== expectOrigin) throw new Error("Origin mismatch");
  return cd;
}

// ---------- sessions ----------
async function secret(env) {
  let s = await env.KV.get("session-secret");
  if (!s) { s = b64u.enc(crypto.getRandomValues(new Uint8Array(32))); await env.KV.put("session-secret", s); }
  return crypto.subtle.importKey("raw", b64u.dec(s), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export async function makeSession(env, player, days = 30) {
  const body = b64u.enc(te.encode(JSON.stringify({ p: player, iat: Date.now(), exp: Date.now() + days * 864e5 })));
  const sig = b64u.enc(await crypto.subtle.sign("HMAC", await secret(env), te.encode(body)));
  return `bb=${body}.${sig}; Path=/; Max-Age=${days * 86400}; HttpOnly; Secure; SameSite=Lax`;
}
export const clearSession = () => "bb=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax";

export async function readSession(request, env) {
  const m = /(?:^|;\s*)bb=([^;]+)/.exec(request.headers.get("cookie") || "");
  if (!m) return null;
  const [body, sig] = m[1].split(".");
  if (!body || !sig) return null;
  try {
    const ok = await crypto.subtle.verify("HMAC", await secret(env), b64u.dec(sig), te.encode(body));
    if (!ok) return null;
    const data = JSON.parse(new TextDecoder().decode(b64u.dec(body)));
    if (data.exp < Date.now()) return null;
    // Session dies if the host released this name (or it was re-claimed after this session was issued).
    const claim = await getClaim(env, data.p);
    if (!claim || claim.at > (data.iat || 0) + 1000) return null;
    return data.p;
  } catch { return null; }
}

// ---------- KV layout ----------
// chal:<id>      -> { c, player?, kind }   (5 min TTL)
// cred:<credId>  -> { player, alg, jwk, counter }
// claim:<slug>   -> { player, creds: [credId...], at }
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
export const claimKey = (p) => "claim:" + slug(p);

export async function getClaim(env, player) {
  return env.KV.get(claimKey(player), "json");
}

// How each name is locked: "passkey", "password", "both", or false if unclaimed.
const lockKind = (c) => {
  if (!c) return false;
  const pk = (c.creds || []).length > 0, pw = !!c.pw;
  return pk && pw ? "both" : pw ? "password" : "passkey";
};

export async function claimedPlayers(env, players) {
  const out = {};
  await Promise.all(players.map(async (p) => { out[p] = lockKind(await getClaim(env, p)); }));
  return out;
}

export async function releasePlayer(env, player) {
  const c = await getClaim(env, player);
  if (c) for (const id of c.creds || []) await env.KV.delete("cred:" + id);
  await env.KV.delete(claimKey(player));
  await env.KV.delete(failKey(player));
}

// ---------- passwords ----------
// Stored as a salted PBKDF2-SHA256 hash inside the claim; the password itself is never kept.
// 100,000 iterations is the most Workers allows.
const PW_ITER = 100000, PW_MIN = 4, PW_MAX = 200;
const MAX_FAILS = 5, LOCKOUT_SECONDS = 300;
const failKey = (p) => "pwfail:" + slug(p);

async function pwHash(password, salt, iter) {
  const key = await crypto.subtle.importKey("raw", te.encode(password), "PBKDF2", false, ["deriveBits"]);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: iter }, key, 256));
}

// Constant-time compare for equal-length byte arrays.
const sameBytes = (a, b) => { if (a.length !== b.length) return false; let d = 0; for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i]; return d === 0; };

export class AuthError extends Error {
  constructor(message, status = 400) { super(message); this.status = status; }
}

export async function passwordRegister(env, player, password) {
  if (typeof password !== "string" || password.length < PW_MIN) throw new AuthError(`At least ${PW_MIN} characters.`);
  if (password.length > PW_MAX) throw new AuthError("That password is too long.");
  if (await getClaim(env, player)) throw new AuthError("That name is already claimed. If it's you, sign in instead.", 409);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const hash = await pwHash(password, salt, PW_ITER);
  await env.KV.put(claimKey(player), JSON.stringify({ player, creds: [], pw: { salt: b64u.enc(salt), hash: b64u.enc(hash), iter: PW_ITER }, at: Date.now() }));
  return player;
}

export async function passwordLogin(env, player, password) {
  const c = await getClaim(env, player);
  if (!c) throw new AuthError("That name hasn't been claimed yet.", 404);
  if (!c.pw) throw new AuthError("This name is locked with Face ID, not a password.", 400);
  const fails = (await env.KV.get(failKey(player), "json")) || { n: 0 };
  if (fails.n >= MAX_FAILS) throw new AuthError("Too many wrong tries. Wait 5 minutes or ask the host.", 429);
  const hash = await pwHash(String(password || ""), b64u.dec(c.pw.salt), c.pw.iter || PW_ITER);
  if (!sameBytes(hash, b64u.dec(c.pw.hash))) {
    await env.KV.put(failKey(player), JSON.stringify({ n: fails.n + 1 }), { expirationTtl: LOCKOUT_SECONDS });
    throw new AuthError("Wrong password.", 401);
  }
  if (fails.n) await env.KV.delete(failKey(player));
  return player;
}

async function newChallenge(env, data) {
  const id = b64u.enc(crypto.getRandomValues(new Uint8Array(16)));
  const c = b64u.enc(crypto.getRandomValues(new Uint8Array(32)));
  await env.KV.put("chal:" + id, JSON.stringify({ c, ...data }), { expirationTtl: 300 });
  return { id, c };
}
async function takeChallenge(env, id) {
  const v = await env.KV.get("chal:" + id, "json");
  if (v) await env.KV.delete("chal:" + id);
  return v;
}

// ---------- ceremonies ----------
export async function registerOptions(env, url, player) {
  const taken = lockKind(await getClaim(env, player));
  if (taken) {
    const how = taken === "password" ? "your password" : taken === "both" ? "Face ID or your password" : "Face ID";
    throw new AuthError(`That name is already claimed. If it's you, sign in with ${how}.`, 409);
  }
  const { id, c } = await newChallenge(env, { kind: "reg", player });
  return {
    challengeId: id,
    publicKey: {
      challenge: c,
      rp: { id: url.hostname, name: "Bachelor Bingo" },
      user: { id: b64u.enc(te.encode(slug(player))), name: player, displayName: player },
      pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
      authenticatorSelection: { authenticatorAttachment: "platform", residentKey: "required", userVerification: "required" },
      attestation: "none",
      timeout: 60000,
    },
  };
}

export async function registerFinish(env, url, body) {
  const ch = await takeChallenge(env, body.challengeId);
  if (!ch || ch.kind !== "reg") throw new AuthError("Expired. Try again.");
  const player = ch.player;
  if (await getClaim(env, player)) throw new AuthError("That name was just claimed by someone else.", 409);
  parseClientData(b64u.dec(body.response.clientDataJSON), "webauthn.create", ch.c, url.origin);
  const { value: att } = cborDecode(b64u.dec(body.response.attestationObject));
  const ad = parseAuthData(att.get("authData"));
  if (!eq(ad.rpIdHash, await sha256(te.encode(url.hostname)))) throw new Error("Wrong site");
  if (!ad.up || !ad.uv) throw new Error("Face ID / passcode verification is required");
  if (!ad.cred) throw new Error("No credential returned");
  const credId = b64u.enc(ad.cred.credId);
  const { alg, jwk } = coseToJwk(ad.cred.cose);
  await env.KV.put("cred:" + credId, JSON.stringify({ player, alg, jwk, counter: ad.counter }));
  await env.KV.put(claimKey(player), JSON.stringify({ player, creds: [credId], at: Date.now() }));
  return player;
}

export async function loginOptions(env, url, player) {
  let allow = [];
  if (player) {
    const c = await getClaim(env, player);
    if (!c) throw new AuthError("That name hasn't been claimed yet.", 404);
    if (!(c.creds || []).length) throw new AuthError("This name is locked with a password, not Face ID.");
    allow =(c.creds || []).map((id) => ({ type: "public-key", id, transports: ["internal", "hybrid"] }));
  }
  const { id, c } = await newChallenge(env, { kind: "login", player: player || null });
  return { challengeId: id, publicKey: { challenge: c, rpId: url.hostname, allowCredentials: allow, userVerification: "required", timeout: 60000 } };
}

export async function loginFinish(env, url, body) {
  const ch = await takeChallenge(env, body.challengeId);
  if (!ch || ch.kind !== "login") throw new AuthError("Expired. Try again.");
  const credId = body.id;
  const cred = await env.KV.get("cred:" + credId, "json");
  if (!cred) throw new AuthError("This passkey isn't registered here. Pick your name to set it up, or ask the host to release it.", 404);
  if (ch.player && ch.player !== cred.player) throw new AuthError("That passkey belongs to a different name.", 403);
  const cdj = b64u.dec(body.response.clientDataJSON);
  parseClientData(cdj, "webauthn.get", ch.c, url.origin);
  const adBytes = b64u.dec(body.response.authenticatorData);
  const ad = parseAuthData(adBytes);
  if (!eq(ad.rpIdHash, await sha256(te.encode(url.hostname)))) throw new Error("Wrong site");
  if (!ad.up || !ad.uv) throw new Error("Face ID / passcode verification is required");
  const data = new Uint8Array(adBytes.length + 32);
  data.set(adBytes, 0); data.set(await sha256(cdj), adBytes.length);
  const ok = await verifySig(cred.alg, cred.jwk, data, b64u.dec(body.response.signature));
  if (!ok) throw new Error("Signature check failed");
  if (ad.counter && cred.counter && ad.counter <= cred.counter) throw new Error("Replayed credential");
  cred.counter = ad.counter;
  await env.KV.put("cred:" + credId, JSON.stringify(cred));
  return cred.player;
}
