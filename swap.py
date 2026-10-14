# Usage: python3 swap.py dummy|real [--test]
# Replaces the live game's players + clues, regenerates boards, wipes marks/photos.
# --test (with dummy) also runs an end-to-end mark/bingo check and wipes it after.
import json, os, sys, urllib.request, struct, zlib
B = "https://bachelor-bingo.jjworker99.workers.dev"; PIN = os.environ.get("HOST_PIN", "ribeye")
UA = "Mozilla/5.0 (bachelor-bingo script)"  # Cloudflare blocks Python's default user agent (error 1010)
which = sys.argv[1] if len(sys.argv) > 1 else "dummy"
def call(path, data=None, method=None):
    req = urllib.request.Request(B + path, data=json.dumps(data).encode() if data is not None else None,
        headers={"content-type": "application/json", "x-pin": PIN, "user-agent": UA}, method=method or ("POST" if data is not None else "GET"))
    try: return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e: return json.load(e)
cfg = json.load(open(f"data-{which}.json"))
print("reset:", call("/api/host/reset", {"player": "*"}), "release:", call("/api/host/release", {"player": "*"}))
print("config:", call("/api/host/config", {**cfg, "regenerate": True}))
s = call("/api/state"); print("live now:", s["title"], "|", len(s["players"]), "players |", s["clueCount"], "clues")
h = call("/api/host/config"); print("usable per player (need %d):" % h["need"], h["usable"])
if "--test" in sys.argv:
    def png():
        raw = b"".join(b"\x00" + b"\xf5\xc2\x42" * 8 for _ in range(8))
        ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")
    def mark(player, clue, witness):
        bd = b"--X\r\n"
        for k, v in [("player", player), ("clue", clue), ("witness", witness)]:
            bd += f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n--X\r\n'.encode()
        bd += b'Content-Disposition: form-data; name="photo"; filename="p.png"\r\nContent-Type: image/png\r\n\r\n' + png() + b"\r\n--X--\r\n"
        req = urllib.request.Request(B + "/api/mark", data=bd, headers={"content-type": "multipart/form-data; boundary=X", "x-pin": PIN, "user-agent": UA}, method="POST")
        try: return json.load(urllib.request.urlopen(req))
        except urllib.error.HTTPError as e: return json.load(e)
    p, w = cfg["players"][0], cfg["players"][1]
    board = call(f"/api/state?player={p}")["board"]
    col = [c for c in board if c["cell"] % 4 == 0 and c.get("id")]
    print("self-witness rejected:", mark(p, col[0]["id"], p))
    for c in col: print("mark:", mark(p, c["id"], w).get("newBingo"))
    print("bingo:", call(f"/api/state?player={p}")["bingo"])
    print("leader:", call("/api/leaderboard")["rows"][0])
    ph = call("/api/photos")["items"]; print("photos:", len(ph))
    r = urllib.request.urlopen(urllib.request.Request(B + ph[0]["url"], headers={"user-agent": UA})); print("photo served:", r.status, r.headers["content-type"])
    print("cleanup:", call("/api/host/reset", {"player": "*"}), "photos after:", len(call("/api/photos")["items"]))
