import APP_HTML from "./app.html";
import { PLAYERS, ALIASES, CLUES } from "./seed.js";
import { makeSession, clearSession, readSession, claimedPlayers, releasePlayer, registerOptions, registerFinish, loginOptions, loginFinish, passwordRegister, passwordLogin, AuthError } from "./auth.js";

const SIZE = 4;
const CELLS = SIZE * SIZE;
const FREE = 5; // row 2, col 2 — same spot on every board
const MAX_PHOTO = 25 * 1024 * 1024;

// ---------- helpers ----------

const json = (data, status = 200, extra = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...extra },
  });

const bad = (msg, status = 400) => json({ error: msg }, status);

function autoTag(text, players) {
  const t = " " + text.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ") + " ";
  const about = [];
  for (const p of players) {
    const names = ALIASES[p] || [p.toLowerCase()];
    if (names.some((n) => t.includes(" " + n + " "))) about.push(p);
  }
  return about;
}

function hash32(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffle(arr, rand) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function usableClues(cfg, player) {
  return cfg.clues.filter((c) => !c.about.includes(player));
}

function buildBoard(cfg, player) {
  const rand = rng(hash32(player + "|" + cfg.version));
  const picks = shuffle(usableClues(cfg, player).map((c) => c.id), rand).slice(0, CELLS - 1);
  const board = [];
  let k = 0;
  for (let i = 0; i < CELLS; i++) {
    if (i === FREE) board.push("FREE");
    else board.push(picks[k++] ?? null);
  }
  return board;
}

function buildBoards(cfg) {
  const boards = {};
  for (const p of cfg.players) boards[p] = buildBoard(cfg, p);
  return { version: cfg.version, boards };
}

const LINES = (() => {
  const lines = [];
  for (let r = 0; r < SIZE; r++) lines.push([...Array(SIZE)].map((_, c) => r * SIZE + c));
  for (let c = 0; c < SIZE; c++) lines.push([...Array(SIZE)].map((_, r) => r * SIZE + c));
  lines.push([...Array(SIZE)].map((_, i) => i * SIZE + i));
  lines.push([...Array(SIZE)].map((_, i) => i * SIZE + (SIZE - 1 - i)));
  return lines;
})();

function findBingo(board, marks) {
  const marked = board.map((id, i) => i === FREE || (id && marks[id]));
  for (const line of LINES) if (line.every((i) => marked[i])) return line;
  return null;
}

function slug(s) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

// ---------- state ----------

async function loadConfig(env) {
  let cfg = await env.KV.get("cfg", "json");
  if (!cfg) {
    cfg = {
      players: PLAYERS.slice(),
      clues: CLUES.map((text, i) => ({ id: "c" + (i + 1), text, about: autoTag(text, PLAYERS) })),
      nextId: CLUES.length + 1,
      locked: false,
      version: 1,
      title: "Phillip's Bachelor Party Bingo",
    };
    await env.KV.put("cfg", JSON.stringify(cfg));
  }
  return cfg;
}

async function loadBoards(env, cfg) {
  let b = await env.KV.get("boards", "json");
  if (!b || b.version !== cfg.version) {
    b = buildBoards(cfg);
    await env.KV.put("boards", JSON.stringify(b));
  }
  // Players added without a version bump still need a board.
  let dirty = false;
  for (const p of cfg.players) if (!b.boards[p]) { b.boards[p] = buildBoard(cfg, p); dirty = true; }
  if (dirty) await env.KV.put("boards", JSON.stringify(b));
  return b;
}

const marksKey = (p) => "marks:" + slug(p);
const bingoKey = (p) => "bingo:" + slug(p);

async function loadMarks(env, player) {
  return (await env.KV.get(marksKey(player), "json")) || {};
}

async function allMarks(env, cfg) {
  const out = {};
  await Promise.all(cfg.players.map(async (p) => { out[p] = await loadMarks(env, p); }));
  return out;
}

async function allBingos(env, cfg) {
  const out = {};
  await Promise.all(cfg.players.map(async (p) => {
    const b = await env.KV.get(bingoKey(p), "json");
    if (b) out[p] = b;
  }));
  return out;
}

function clueMap(cfg) {
  const m = {};
  for (const c of cfg.clues) m[c.id] = c;
  return m;
}

function leaderboard(cfg, boards, marks, bingos) {
  const rows = cfg.players.map((p) => {
    const board = boards.boards[p] || [];
    const m = marks[p] || {};
    const count = board.filter((id, i) => i !== FREE && id && m[id]).length;
    return { player: p, marked: count, bingoAt: bingos[p]?.at || null, line: bingos[p]?.line || null };
  });
  rows.sort((a, b) => {
    if (a.bingoAt && b.bingoAt) return a.bingoAt - b.bingoAt;
    if (a.bingoAt) return -1;
    if (b.bingoAt) return 1;
    return b.marked - a.marked || a.player.localeCompare(b.player);
  });
  return rows;
}

function isHost(request, env) {
  const url = new URL(request.url);
  const pin = request.headers.get("x-pin") || url.searchParams.get("pin") || "";
  return !!env.HOST_PIN && pin === env.HOST_PIN;
}

// ---------- routes ----------

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    try {
      if (method === "GET" && (path === "/" || path === "/index.html")) {
        return new Response(APP_HTML, {
          headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" },
        });
      }

      if (method === "GET" && path.startsWith("/photo/")) {
        const key = decodeURIComponent(path.slice("/photo/".length));
        const obj = await env.PHOTOS.get(key);
        if (!obj) return new Response("Not found", { status: 404 });
        return new Response(obj.body, {
          headers: {
            "content-type": obj.httpMetadata?.contentType || "image/jpeg",
            "cache-control": "public, max-age=31536000, immutable",
            etag: obj.httpEtag,
          },
        });
      }

      // ----- auth -----
      if (path.startsWith("/api/auth/")) try {
        const cfg = await loadConfig(env);
        if (method === "POST" && path === "/api/auth/register/options") {
          const { player } = await request.json();
          if (!cfg.players.includes(player)) return bad("Unknown player.");
          return json(await registerOptions(env, url, player));
        }
        if (method === "POST" && path === "/api/auth/register") {
          const player = await registerFinish(env, url, await request.json());
          return json({ ok: true, player }, 200, { "set-cookie": await makeSession(env, player) });
        }
        if (method === "POST" && path === "/api/auth/login/options") {
          const { player } = await request.json().catch(() => ({}));
          return json(await loginOptions(env, url, player || null));
        }
        if (method === "POST" && path === "/api/auth/login") {
          const player = await loginFinish(env, url, await request.json());
          return json({ ok: true, player }, 200, { "set-cookie": await makeSession(env, player) });
        }
        if (method === "POST" && (path === "/api/auth/password/register" || path === "/api/auth/password/login")) {
          const { player, password } = await request.json().catch(() => ({}));
          if (!cfg.players.includes(player)) return bad("Unknown player.");
          if (path.endsWith("/register")) await passwordRegister(env, player, password);
          else await passwordLogin(env, player, password);
          return json({ ok: true, player }, 200, { "set-cookie": await makeSession(env, player) });
        }
        if (method === "POST" && path === "/api/auth/logout") {
          return json({ ok: true }, 200, { "set-cookie": clearSession() });
        }
        return bad("Not found", 404);
      } catch (e) {
        // Sign-in problems go back to the page as plain messages, not "Something broke".
        return bad(e instanceof AuthError ? e.message : "Sign-in didn't work: " + (e?.message || e) + ". Try again.", e instanceof AuthError ? e.status : 400);
      }

      if (method === "GET" && path === "/api/state") {
        const cfg = await loadConfig(env);
        const boards = await loadBoards(env, cfg);
        const host = isHost(request, env);
        const me = await readSession(request, env);
        // Host may view any board via ?player=; everyone else sees only their own.
        const player = host && url.searchParams.get("player") ? url.searchParams.get("player") : me;
        const [bingos, claimed] = await Promise.all([allBingos(env, cfg), claimedPlayers(env, cfg.players)]);
        const base = { title: cfg.title, players: cfg.players, claimed, me, host, locked: cfg.locked, clueCount: cfg.clues.length, bingos };
        if (!player || !cfg.players.includes(player)) return json(base);
        const cm = clueMap(cfg);
        const marks = await loadMarks(env, player);
        const board = boards.boards[player].map((id, i) => {
          if (i === FREE) return { cell: i, free: true };
          if (!id || !cm[id]) return { cell: i, blank: true };
          return { cell: i, id, text: cm[id].text, mark: marks[id] || null };
        });
        return json({ ...base, player, board, bingo: bingos[player] || null, line: findBingo(boards.boards[player], marks) });
      }

      if (method === "GET" && path === "/api/leaderboard") {
        const cfg = await loadConfig(env);
        const boards = await loadBoards(env, cfg);
        const [marks, bingos] = await Promise.all([allMarks(env, cfg), allBingos(env, cfg)]);
        return json({ locked: cfg.locked, rows: leaderboard(cfg, boards, marks, bingos) });
      }

      if (method === "GET" && path === "/api/photos") {
        const cfg = await loadConfig(env);
        const cm = clueMap(cfg);
        const marks = await allMarks(env, cfg);
        const items = [];
        for (const [player, m] of Object.entries(marks)) {
          for (const [id, mark] of Object.entries(m)) {
            if (!mark.key) continue;
            items.push({ player, clue: cm[id]?.text || "(removed clue)", witness: mark.witness, at: mark.at, url: "/photo/" + encodeURIComponent(mark.key), thumb: mark.thumb ? "/photo/" + encodeURIComponent(mark.thumb) : null });
          }
        }
        items.sort((a, b) => b.at - a.at);
        return json({ items });
      }

      if (method === "POST" && path === "/api/mark") {
        const cfg = await loadConfig(env);
        if (cfg.locked) return bad("The game is locked right now.", 423);
        const form = await request.formData();
        const me = await readSession(request, env);
        const host = isHost(request, env);
        const player = host && form.get("player") ? String(form.get("player")) : me;
        if (!player) return bad("Sign in with Face ID first.", 401);
        const id = String(form.get("clue") || "");
        const witness = String(form.get("witness") || "");
        const photo = form.get("photo");
        if (!cfg.players.includes(player)) return bad("Unknown player.");
        if (!cfg.players.includes(witness) || witness === player) return bad("Pick a witness who isn't you.");
        const boards = await loadBoards(env, cfg);
        if (!boards.boards[player].includes(id)) return bad("That clue isn't on your board.");
        if (!(photo instanceof File) || photo.size === 0) return bad("A photo is required.");
        if (photo.size > MAX_PHOTO) return bad("Photo is too large (25 MB max).");
        const thumb = form.get("thumb");
        const marks = await loadMarks(env, player);
        if (marks[id]) return bad("Already marked.");
        const ext = (photo.type === "image/png") ? "png" : (photo.type === "image/webp") ? "webp" : "jpg";
        const key = `photos/${slug(player)}/${id}-${Date.now()}.${ext}`;
        await env.PHOTOS.put(key, photo.stream(), { httpMetadata: { contentType: photo.type || "image/jpeg" } });
        let thumbKey = null;
        if (thumb instanceof File && thumb.size > 0 && thumb.size < 2 * 1024 * 1024) {
          thumbKey = key.replace(/\.(\w+)$/, "-thumb.jpg");
          await env.PHOTOS.put(thumbKey, thumb.stream(), { httpMetadata: { contentType: "image/jpeg" } });
        }
        marks[id] = { key, thumb: thumbKey, witness, at: Date.now() };
        await env.KV.put(marksKey(player), JSON.stringify(marks));
        let bingo = await env.KV.get(bingoKey(player), "json");
        let fresh = false;
        if (!bingo) {
          const line = findBingo(boards.boards[player], marks);
          if (line) {
            bingo = { at: Date.now(), line };
            await env.KV.put(bingoKey(player), JSON.stringify(bingo));
            fresh = true;
          }
        }
        return json({ ok: true, mark: marks[id], bingo, newBingo: fresh });
      }

      if (method === "DELETE" && path === "/api/mark") {
        const cfg = await loadConfig(env);
        const host = isHost(request, env);
        const me = await readSession(request, env);
        const player = host && url.searchParams.get("player") ? url.searchParams.get("player") : (me || "");
        if (!player) return bad("Sign in with Face ID first.", 401);
        const id = url.searchParams.get("clue") || "";
        if (cfg.locked && !host) return bad("The game is locked right now.", 423);
        if (!cfg.players.includes(player)) return bad("Unknown player.");
        const marks = await loadMarks(env, player);
        const mark = marks[id];
        if (!mark) return json({ ok: true });
        // Players can undo their own marks for 10 minutes; the host can always.
        if (!host && Date.now() - mark.at > 10 * 60 * 1000) return bad("Too late to undo that one. Ask the host.", 403);
        delete marks[id];
        await env.KV.put(marksKey(player), JSON.stringify(marks));
        if (mark.key) await env.PHOTOS.delete(mark.key);
        if (mark.thumb) await env.PHOTOS.delete(mark.thumb);
        // If the bingo no longer holds, drop it.
        const bingo = await env.KV.get(bingoKey(player), "json");
        if (bingo) {
          const boards = await loadBoards(env, cfg);
          if (!findBingo(boards.boards[player], marks)) await env.KV.delete(bingoKey(player));
        }
        return json({ ok: true });
      }

      // ----- host -----
      if (path.startsWith("/api/host")) {
        if (!isHost(request, env)) return bad("Wrong PIN.", 401);
        const cfg = await loadConfig(env);

        if (method === "GET" && path === "/api/host/config") {
          const boards = await loadBoards(env, cfg);
          const [marks, bingos] = await Promise.all([allMarks(env, cfg), allBingos(env, cfg)]);
          const usable = {};
          for (const p of cfg.players) usable[p] = usableClues(cfg, p).length;
          const cm = clueMap(cfg);
          const detail = {};
          for (const p of cfg.players) {
            detail[p] = Object.entries(marks[p] || {}).map(([id, m]) => ({ id, clue: cm[id]?.text || "?", witness: m.witness, at: m.at }));
          }
          const claimed = await claimedPlayers(env, cfg.players);
          return json({ cfg, usable, claimed, need: CELLS - 1, leaderboard: leaderboard(cfg, boards, marks, bingos), marks: detail });
        }

        if (method === "POST" && path === "/api/host/release") {
          const body = await request.json();
          const targets = body.player === "*" ? cfg.players : [body.player];
          for (const p of targets) await releasePlayer(env, p);
          return json({ ok: true });
        }

        if (method === "POST" && path === "/api/host/config") {
          const body = await request.json();
          const next = { ...cfg };
          if (Array.isArray(body.players)) {
            next.players = [...new Set(body.players.map((p) => String(p).trim()).filter(Boolean))];
          }
          if (Array.isArray(body.clues)) {
            next.clues = body.clues.map((c) => {
              const text = String(c.text || "").trim();
              const about = Array.isArray(c.about) ? c.about.filter((p) => next.players.includes(p)) : autoTag(text, next.players);
              return { id: c.id || "c" + next.nextId++, text, about };
            }).filter((c) => c.text);
          }
          if (typeof body.locked === "boolean") next.locked = body.locked;
          if (typeof body.title === "string" && body.title.trim()) next.title = body.title.trim();
          if (body.regenerate) next.version = cfg.version + 1;
          await env.KV.put("cfg", JSON.stringify(next));
          if (body.regenerate) await env.KV.put("boards", JSON.stringify(buildBoards(next)));
          return json({ ok: true, version: next.version });
        }

        if (method === "POST" && path === "/api/host/autotag") {
          const body = await request.json();
          return json({ about: autoTag(String(body.text || ""), cfg.players) });
        }

        if (method === "POST" && path === "/api/host/reset") {
          const body = await request.json();
          const targets = body.player === "*" ? cfg.players : [body.player];
          for (const p of targets) {
            if (!cfg.players.includes(p)) continue;
            const marks = await loadMarks(env, p);
            for (const m of Object.values(marks)) { if (m.key) await env.PHOTOS.delete(m.key); if (m.thumb) await env.PHOTOS.delete(m.thumb); }
            await env.KV.delete(marksKey(p));
            await env.KV.delete(bingoKey(p));
          }
          return json({ ok: true });
        }
      }

      return new Response("Not found", { status: 404 });
    } catch (err) {
      return json({ error: "Something broke: " + (err?.message || err) }, 500);
    }
  },
};
