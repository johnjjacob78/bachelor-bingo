"""Deep tests for tap-your-name sign-in.

The first phone to tap a name owns it, through a signed session cookie. These tests cover claiming,
other phones, races, host release, sign out, cookie tampering, and the KV read/write budget.

Run against a local `wrangler dev` (see tests/README.md). Clears all marks, photos and name claims.
"""
import base64, json, os, sys, threading, urllib.request
from playwright.sync_api import sync_playwright

B = os.environ.get("BINGO_URL", "http://localhost:8787")
PIN = os.environ.get("HOST_PIN", "ribeye")

# --- network setup: lets the suites run against the live site too ---
import urllib.request as _ur
_op = _ur.build_opener(); _op.addheaders = [("User-Agent", "Mozilla/5.0 (bingo-tests)")]  # Cloudflare blocks Python's default UA
_ur.install_opener(_op)
_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
LAUNCH_KW = {"proxy": {"server": _PROXY}} if _PROXY and not B.startswith("http://localhost") else {}
CTX_KW = {"ignore_https_errors": True} if LAUNCH_KW else {}
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def call(path, data=None, method=None, pin=True, cookie=None, raw=False):
    hdr = {"content-type": "application/json"}
    if pin: hdr["x-pin"] = PIN
    if cookie: hdr["cookie"] = cookie
    req = urllib.request.Request(B + path, data=json.dumps(data).encode() if data is not None else None, headers=hdr,
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        r = urllib.request.urlopen(req)
        out = (r.status, json.load(r), r.headers)
    except urllib.error.HTTPError as e:
        out = (e.code, json.load(e), e.headers)
    return out if raw else out[:2]


def claim_api(player):
    """Claims over the API, returns (status, cookie-or-error)."""
    st, body, h = call("/api/auth/claim", {"player": player}, pin=False, raw=True)
    sc = h.get("set-cookie") or ""
    return st, (sc.split(";")[0] if sc.startswith("bb=") else body.get("error"))


def owner(cookie):
    """Whose board this cookie opens, or None."""
    return call("/api/state", pin=False, cookie=cookie)[1].get("player")


def mark_api(player, clue, witness):
    """Host-marks a square for a player, with a tiny photo. Returns HTTP status."""
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (32, 32), (200, 50, 50)).save(buf, "JPEG")
    bd = b"--X\r\n"
    for k, v in [("player", player), ("clue", clue), ("witness", witness)]:
        bd += f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n--X\r\n'.encode()
    bd += b'Content-Disposition: form-data; name="photo"; filename="p.jpg"\r\nContent-Type: image/jpeg\r\n\r\n' + buf.getvalue() + b"\r\n--X--\r\n"
    req = urllib.request.Request(B + "/api/mark", data=bd, headers={"content-type": "multipart/form-data; boundary=X", "x-pin": PIN}, method="POST")
    try: return urllib.request.urlopen(req).status
    except urllib.error.HTTPError as e: return e.code


def kv(h): r, w = h["x-kv"].split(); return int(r[2:]), int(w[2:])
def b64u_dec(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
def b64u_enc(b): return base64.urlsafe_b64encode(b).decode().rstrip("=")
def claimed(): return call("/api/host/config")[1]["claimed"]


def main():
    call("/api/host/reset", {"player": "*"}); call("/api/host/release", {"player": "*"}); call("/api/host/config", {"locked": False})

    # ---------------- API level ----------------
    st, ck = claim_api("John")
    check("claim: tapping a free name gives a session", st == 200 and ck and ck.startswith("bb="), f"{st} {ck}")
    check("claim: that session opens John's board", owner(ck) == "John")
    check("claim: John now shows as taken", claimed()["John"] is True)
    st, err = claim_api("John")
    check("claim: a second claim on John is refused (409)", st == 409 and "taken" in err, f"{st} {err}")
    check("claim: and the first session is untouched", owner(ck) == "John")
    st, err = claim_api("Nobody")
    check("claim: unknown name refused", st == 400, f"{st} {err}")
    st, err = claim_api("")
    check("claim: empty name refused", st == 400, f"{st} {err}")

    # cookie tampering
    body, sig = ck[3:].split(".")
    payload = json.loads(b64u_dec(body)); payload["p"] = "Phillip"
    forged = "bb=" + b64u_enc(json.dumps(payload, separators=(",", ":")).encode()) + "." + sig
    check("cookie: changing the name inside the cookie is rejected", owner(forged) is None)
    check("cookie: garbage cookie rejected", owner("bb=abc.def") is None)
    check("cookie: no cookie means no board", owner(None) is None)
    forged2 = "bb=" + body + "." + b64u_enc(b"\x00" * 32)
    check("cookie: bad signature rejected", owner(forged2) is None)

    # race: many phones tap the same free name at the same instant
    outs = []
    def tap(): outs.append(claim_api("Kush P"))
    ts = [threading.Thread(target=tap) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    live = [c for st, c in outs if st == 200 and owner(c) == "Kush P"]
    check("race: 8 phones tap Kush P at once -> exactly one owns it", len(live) == 1, str([s for s, _ in outs]))
    check("race: everyone else is refused or signed out", sum(1 for st, c in outs if st == 200 and owner(c) == "Kush P") == 1)

    # release
    call("/api/host/release", {"player": "John"})
    check("release: John's session dies immediately", owner(ck) is None)
    check("release: John shows as free", claimed()["John"] is False)
    st, ck2 = claim_api("John")
    check("release: John can be claimed again", st == 200 and owner(ck2) == "John")
    check("release: the OLD cookie stays dead after re-claim", owner(ck) is None)

    # sign out frees the name
    st, _ = call("/api/auth/logout", {}, pin=False, cookie=ck2)
    check("sign out: OK", st == 200)
    check("sign out: session dead", owner(ck2) is None)
    check("sign out: John is free again", claimed()["John"] is False)
    st, _ = call("/api/auth/logout", {}, pin=False, cookie=ck2)
    check("sign out: signing out twice is harmless", st == 200)
    st, ck3 = claim_api("Jay")
    st, _ = call("/api/auth/logout", {}, pin=False, cookie=ck)  # dead cookie
    check("sign out: a dead cookie can't release someone else's name", claimed()["Jay"] is True and owner(ck3) == "Jay")

    # removing a player from the list
    cfg = call("/api/host/config")[1]["cfg"]
    call("/api/host/config", {"players": [p for p in cfg["players"] if p != "Jay"]})
    check("players: removed player's session no longer opens a board", "board" not in call("/api/state", pin=False, cookie=ck3)[1])
    call("/api/host/config", {"players": cfg["players"]})
    check("players: re-added player shows correct claim", claimed()["Jay"] is True)

    # consistency: nothing read stale, nothing lost (the live KV version lost 3 of 4 rapid marks)
    stable = [owner(ck3) for _ in range(12)]
    check("consistency: a fresh sign-in stays valid on every request", all(o == "Jay" for o in stable), str(stable))
    idx = [claimed()["Jay"] for _ in range(6)]
    check("consistency: name list shows Jay taken on every request", all(idx), str(idx))
    call("/api/host/reset", {"player": "Kaushik"})
    ids = [c["id"] for c in call("/api/state?player=Kaushik")[1]["board"]]
    sts = [mark_api("Kaushik", cid, "John") for cid in ids[:4]]
    got = len(call("/api/host/config")[1]["marks"]["Kaushik"])
    check("consistency: 4 marks in quick succession all saved", sts == [200] * 4 and got == 4, f"{sts} saved={got}")
    outs = []
    ts = [threading.Thread(target=lambda c=c: outs.append(mark_api("Kaushik", c, "Jay"))) for c in ids[4:10]]
    [t.start() for t in ts]; [t.join() for t in ts]
    got = len(call("/api/host/config")[1]["marks"]["Kaushik"])
    check("consistency: 6 marks at the same instant all saved", outs.count(200) == 6 and got == 10, f"{outs} saved={got}")
    dup = []
    ts = [threading.Thread(target=lambda: dup.append(mark_api("Kaushik", ids[10], "Jay"))) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    km = call("/api/host/config")[1]["marks"]["Kaushik"]
    check("consistency: same square marked 4x at once -> saved once", dup.count(200) == 1 and len(km) == 11, f"{sorted(dup)} saved={len(km)}")
    photos = [p for p in call("/api/photos", pin=False)[1]["items"] if p["player"] == "Kaushik"]
    check("consistency: photo wall shows each mark exactly once", len(photos) == 11, str(len(photos)))
    call("/api/host/reset", {"player": "Kaushik"})

    # budget
    st, d, h = call("/api/state", pin=False, cookie=ck3, raw=True)
    check("budget: signed-in board refresh <= 6 KV reads, 0 writes", kv(h)[0] <= 6 and kv(h)[1] == 0, h["x-kv"])
    st, d, h = call("/api/state", pin=False, raw=True)
    check("budget: signed-out page <= 4 KV reads", kv(h)[0] <= 4, h["x-kv"])
    st, d, h = call("/api/auth/claim", {"player": "Brendan"}, pin=False, raw=True)
    check("budget: claiming a name <= 2 writes", kv(h)[1] <= 2, h["x-kv"])
    st, d, h = call("/api/leaderboard", pin=False, raw=True)
    check("budget: standings <= 17 KV reads", kv(h)[0] <= 17, h["x-kv"])

    call("/api/host/release", {"player": "*"})

    # ---------------- Phones ----------------
    with sync_playwright() as p:
        br = p.chromium.launch(**LAUNCH_KW)

        def phone(dialog="accept"):
            ctx = br.new_context(viewport={"width": 390, "height": 844}, **CTX_KW)
            pg = ctx.new_page(); errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("dialog", lambda d: d.accept() if dialog == "accept" else d.dismiss())
            return ctx, pg, errs

        def cookie_of(ctx):
            c = [c for c in ctx.cookies() if c["name"] == "bb"]
            return f"bb={c[0]['value']}" if c else None

        cA, a, eA = phone()
        a.goto(B); a.wait_for_selector("#names button")
        a.click("#names button:has-text('Phillip')"); a.wait_for_selector("#sheet.open")
        check("phone: sheet asks 'Are you Phillip?'", "Are you Phillip?" in a.inner_text("#sheet h3"))
        a.click("#c-no"); a.wait_for_timeout(200)
        check("phone: 'No, go back' closes and claims nothing", not a.is_visible("#sheet.open") and claimed()["Phillip"] is False)
        a.click("#names button:has-text('Phillip')"); a.wait_for_selector("#c-yes"); a.click("#c-yes")
        a.wait_for_selector("#grid .cell[data-id]", timeout=15000)
        check("phone: 'Yes' lands on Phillip's board", a.inner_text("#who").startswith("Phillip"))

        # second phone sees it taken
        cB, b, eB = phone()
        b.goto(B); b.wait_for_selector("#names button")
        check("phone 2: Phillip shows a lock", b.locator("#names button.taken:has-text('Phillip')").count() == 1)
        b.click("#names button:has-text('Phillip')"); b.wait_for_selector("#sheet.open")
        check("phone 2: taken sheet explains, no claim button", "ask the host" in b.inner_text("#sheet") and b.locator("#c-yes").count() == 0)
        b.click("#c-close")

        # both phones open the same free name; second tap loses cleanly
        a2c, a2, e2 = phone()
        a2.goto(B); a2.wait_for_selector("#names button")
        b.click("#names button:has-text('Aneesh')"); b.wait_for_selector("#c-yes")
        a2.click("#names button:has-text('Aneesh')"); a2.wait_for_selector("#c-yes")
        b.click("#c-yes"); b.wait_for_selector("#grid .cell[data-id]", timeout=15000)
        a2.click("#c-yes"); a2.wait_for_function("document.querySelector('#cerr') && document.querySelector('#cerr').textContent.trim() !== ''", timeout=10000)
        check("phone race: slower phone sees 'already taken'", "taken" in a2.inner_text("#cerr"), a2.inner_text("#cerr"))
        a2.click("#c-no"); a2.wait_for_timeout(300)
        check("phone race: slower phone's list now shows Aneesh locked", a2.locator("#names button.taken:has-text('Aneesh')").count() == 1)
        check("phone race: slower phone has no session", cookie_of(a2c) is None or owner(cookie_of(a2c)) is None)

        # release kicks the phone back to the name list on its next refresh
        call("/api/host/release", {"player": "Aneesh"})
        b.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        b.wait_for_selector("#view-pick.active", timeout=10000)
        check("release: phone is sent back to the name list", True)
        check("release: Aneesh is free on that phone's list", b.locator("#names button.taken:has-text('Aneesh')").count() == 0)
        b.click("#names button:has-text('Aneesh')"); b.wait_for_selector("#c-yes"); b.click("#c-yes"); b.wait_for_selector("#grid .cell[data-id]", timeout=15000)
        check("release: same phone can tap the name again", b.inner_text("#who").startswith("Aneesh"))

        # sign out, but cancel the confirm
        cC, c, eC = phone(dialog="dismiss")
        c.goto(B); c.wait_for_selector("#names button")
        c.click("#names button:has-text('Rahul')"); c.wait_for_selector("#c-yes"); c.click("#c-yes"); c.wait_for_selector("#grid .cell[data-id]", timeout=15000)
        c.click("#switch"); c.wait_for_timeout(500)
        check("sign out: cancelling the confirm keeps you signed in", c.inner_text("#who").startswith("Rahul") and claimed()["Rahul"] is True)
        # accepted sign out
        a.click("#switch"); a.wait_for_selector("#view-pick.active")
        check("sign out: accepted -> back to names, Phillip free", claimed()["Phillip"] is False and a.locator("#names button.taken:has-text('Phillip')").count() == 0)
        a.click("#names button:has-text('Diante')"); a.wait_for_selector("#c-yes"); a.click("#c-yes"); a.wait_for_selector("#grid .cell[data-id]", timeout=15000)
        check("sign out: same phone can pick a different name", a.inner_text("#who").startswith("Diante"))

        # host on a phone with no name can still view and fix boards
        cH, hp, eH = phone()
        hp.goto(B); hp.wait_for_selector("#names button")
        hp.click("nav button:has-text('Host')"); hp.fill("#pin", PIN); hp.click("#pin-go"); hp.wait_for_selector("[data-viewboard='Rahul']")
        hp.click("[data-viewboard='Rahul']"); hp.wait_for_selector("#grid .cell[data-id]")
        check("host: phone without a name can view Rahul's board", hp.inner_text("#who").startswith("viewing Rahul"))
        hp.click("#switch"); hp.wait_for_timeout(500)
        check("host: 'Done' doesn't sign anyone out", claimed()["Rahul"] is True)
        hp.click("nav button:has-text('Host')"); hp.wait_for_selector("[data-release='Rahul']"); hp.wait_for_timeout(500)
        hp.click("[data-release='Rahul']"); hp.wait_for_function("!document.querySelector(\"[data-release='Rahul']\")")
        check("host: Release name button works", claimed()["Rahul"] is False)
        c.evaluate("document.dispatchEvent(new Event('visibilitychange'))"); c.wait_for_selector("#view-pick.active", timeout=10000)
        check("host: released phone drops back to the name list", True)

        for nm, e in (("A", eA), ("B", eB), ("A2", e2), ("C", eC), ("H", eH)):
            check(f"no page errors ({nm})", not e, str(e))
        br.close()

    call("/api/host/reset", {"player": "*"}); call("/api/host/release", {"player": "*"})
    fails = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(fails)}/{len(results)} passed")
    for f in fails: print("  FAIL:", f[0], f[2])
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
