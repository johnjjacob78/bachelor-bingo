// Tap-your-name sign-in: the first phone to tap a name owns it, via a signed session cookie.
// No passwords, no Face ID. The host can release a name (wrong tap, new phone, cleared browser).

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

// ---------- sessions ----------
let secretKeyCache = null; // per-isolate; saves a KV read on every request
async function secret(env) {
  if (secretKeyCache) return secretKeyCache;
  let s = await env.KV.get("session-secret");
  if (!s) { s = b64u.enc(crypto.getRandomValues(new Uint8Array(32))); await env.KV.put("session-secret", s); }
  secretKeyCache = await crypto.subtle.importKey("raw", b64u.dec(s), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
  return secretKeyCache;
}

const DAYS = 30;
async function makeSession(env, player, cid) {
  const body = b64u.enc(te.encode(JSON.stringify({ p: player, cid, exp: Date.now() + DAYS * 864e5 })));
  const sig = b64u.enc(await crypto.subtle.sign("HMAC", await secret(env), te.encode(body)));
  return `bb=${body}.${sig}; Path=/; Max-Age=${DAYS * 86400}; HttpOnly; Secure; SameSite=Lax`;
}
export const clearSession = () => "bb=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax";

// Returns the signed-in player, or null. A session is only valid while its claim is the current one,
// so releasing a name (or a later claim winning a race) signs the old phone out on its next request.
export async function readSession(request, env) {
  const m = /(?:^|;\s*)bb=([^;]+)/.exec(request.headers.get("cookie") || "");
  if (!m) return null;
  const [body, sig] = m[1].split(".");
  if (!body || !sig) return null;
  try {
    const ok = await crypto.subtle.verify("HMAC", await secret(env), b64u.dec(sig), te.encode(body));
    if (!ok) return null;
    const data = JSON.parse(new TextDecoder().decode(b64u.dec(body)));
    if (!data.cid || data.exp < Date.now()) return null;
    const claim = await getClaim(env, data.p);
    if (!claim || claim.id !== data.cid) return null;
    return data.p;
  } catch { return null; }
}

// ---------- claims ----------
// claim:<slug> -> { player, id, at }
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
const claimKey = (p) => "claim:" + slug(p);
const getClaim = (env, player) => env.KV.get(claimKey(player), "json");

export class AuthError extends Error {
  constructor(message, status = 400) { super(message); this.status = status; }
}

// Claims a name for this phone and returns a Set-Cookie header.
export async function claimPlayer(env, player) {
  if (await getClaim(env, player)) throw new AuthError(`${player} is already taken on another phone. If that's you, ask the host to release it.`, 409);
  const id = b64u.enc(crypto.getRandomValues(new Uint8Array(12)));
  await env.KV.put(claimKey(player), JSON.stringify({ player, id, at: Date.now() }));
  // KV has no atomic create: if two phones tapped at the same instant, the last write wins. Re-read so a
  // phone that lost the race gets a clear error instead of a session that's already dead.
  const now = await getClaim(env, player);
  if (!now || now.id !== id) throw new AuthError(`${player} was just taken on another phone.`, 409);
  return makeSession(env, player, id);
}

export async function releasePlayer(env, player) {
  await env.KV.delete(claimKey(player));
}

// One KV key ("idx:claims") summarises who's claimed, so a page load costs 1 read instead of 14.
// Per-player claim keys stay the source of truth; the index is rebuilt after every claim or release.
export async function rebuildClaimIndex(env, players) {
  const out = {};
  await Promise.all(players.map(async (p) => { out[p] = !!(await getClaim(env, p)); }));
  await env.KV.put("idx:claims", JSON.stringify(out));
  return out;
}
export async function claimedPlayers(env, players) {
  const idx = await env.KV.get("idx:claims", "json");
  if (!idx || players.some((p) => !(p in idx))) return rebuildClaimIndex(env, players);
  const out = {};
  for (const p of players) out[p] = !!idx[p];
  return out;
}
