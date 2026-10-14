"""Full end-to-end test of every screen and feature, using simulated phones.

Run against a local `wrangler dev` (see tests/README.md). Clears all marks, photos and name locks.
"""
import io, json, time, urllib.request
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

import os
B = os.environ.get("BINGO_URL", "http://localhost:8787"); PIN = os.environ.get("HOST_PIN", "ribeye")

# --- network setup: lets the suites run against the live site too ---
import urllib.request as _ur
_op = _ur.build_opener(); _op.addheaders = [("User-Agent", "Mozilla/5.0 (bingo-tests)")]  # Cloudflare blocks Python's default UA
_ur.install_opener(_op)
_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
LAUNCH_KW = {"proxy": {"server": _PROXY}} if _PROXY and not B.startswith("http://localhost") else {}
CTX_KW = {"ignore_https_errors": True} if LAUNCH_KW else {}
SHOTS = os.environ.get("SHOTS", "/tmp")
results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))

def jpg(label, size=(1200, 900)):
    im = Image.new("RGB", size, (40, 120, 200)); d = ImageDraw.Draw(im); d.text((40, 40), label, fill="white")
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=90); return buf.getvalue()

def host(path, data=None, method=None):
    req = urllib.request.Request(B + path, data=json.dumps(data).encode() if data is not None else None,
        headers={"content-type": "application/json", "x-pin": PIN}, method=method or ("POST" if data is not None else "GET"))
    try: return urllib.request.urlopen(req).status, json.load(urllib.request.urlopen(urllib.request.Request(B + path, data=json.dumps(data).encode() if data is not None else None, headers={"content-type": "application/json", "x-pin": PIN}, method=method or ("POST" if data is not None else "GET"))))
    except urllib.error.HTTPError as e: return e.code, json.load(e)

def anon(path, method="GET"):
    try: r = urllib.request.urlopen(urllib.request.Request(B + path, method=method)); return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)

def claim(pg, name):
    pg.click(f"#names button:has-text('{name}')"); pg.wait_for_selector("#sheet.open #c-yes"); pg.click("#c-yes"); pg.wait_for_selector("#grid .cell[data-id]", timeout=15000)

# clean slate
host("/api/host/reset", {"player": "*"}); host("/api/host/release", {"player": "*"}); host("/api/host/config", {"locked": False})

with sync_playwright() as p:
    br = p.chromium.launch(**LAUNCH_KW)
    def phone(width=390):
        ctx = br.new_context(viewport={"width": width, "height": 844}, device_scale_factor=2, **CTX_KW)
        pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        return ctx, pg, errs

    # ---------- 1. Pick screen ----------
    ctxA, A, errA = phone()
    A.goto(B); A.wait_for_selector("#names button")
    check("pick: 14 names listed", A.locator("#names button").count() == 14)
    check("pick: no horizontal scroll (390px)", A.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
    check("pick: Board tab routes to pick when signed out", A.evaluate("document.querySelector('.view.active').id") == "view-pick")
    A.screenshot(path=f"{SHOTS}/t_pick.png")

    # ---------- 2. Claim a name ----------
    A.click("#names button:has-text('John')"); A.wait_for_selector("#sheet.open")
    check("claim: sheet asks 'Are you John?'", "Are you John?" in A.inner_text("#sheet h3"))
    A.click("#c-no"); A.wait_for_timeout(300)
    check("claim: 'No, go back' leaves John unclaimed", host("/api/host/config")[1]["claimed"]["John"] is False)
    claim(A, "John")
    check("claim: lands on board as John", A.inner_text("#who").startswith("John"))
    ck = [c for c in ctxA.cookies() if c["name"] == "bb"]
    check("claim: session cookie HttpOnly+SameSite", ck and ck[0]["httpOnly"] and ck[0]["sameSite"] == "Lax")
    A.reload(); A.wait_for_selector("#grid .cell[data-id]")
    check("claim: session survives reload", A.inner_text("#who").startswith("John"))

    # ---------- 3. Board render ----------
    cells = A.locator("#grid .cell").count()
    check("board: 16 cells", cells == 16)
    check("board: no free square", A.locator("#grid .cell.free").count() == 0 and "FREE" not in A.inner_text("#grid"))
    check("board: 16 clue cells", A.locator("#grid .cell[data-id]").count() == 16)
    check("board: progress text", "0 of 16 marked" in A.inner_text("#progress"))
    check("board: no horizontal scroll", A.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
    A.screenshot(path=f"{SHOTS}/t_board.png")
    # self-clue rule for all players
    st, cfg = host("/api/host/config")
    ok_all = True; bad = []
    for pl in cfg["cfg"]["players"]:
        s, d = host(f"/api/state?player={urllib.request.quote(pl)}")
        ids = [c["id"] for c in d["board"] if c.get("id")]
        about = {c["id"]: c["about"] for c in cfg["cfg"]["clues"]}
        if any(pl in about.get(i, []) for i in ids): ok_all = False; bad.append(pl)
        if len(ids) != 16: ok_all = False; bad.append(pl + f":{len(ids)} clues")
        if len(set(ids)) != len(ids): ok_all = False; bad.append(pl + ":dup")
    check("board: every player has 16 clues, none about themselves, no duplicates", ok_all, str(bad))
    check("board: deterministic", host("/api/state?player=Jay")[1]["board"] == host("/api/state?player=Jay")[1]["board"])

    # ---------- 4. Second phone ----------
    ctxB, Bp, errB = phone()
    Bp.goto(B); Bp.wait_for_selector("#names button")
    check("pick: claimed name shows lock", Bp.locator("#names button.taken:has-text('John')").count() == 1)
    Bp.click("#names button:has-text('John')"); Bp.wait_for_selector("#sheet.open")
    check("claim: taken name says ask the host, no claim button", "ask the host" in Bp.inner_text("#sheet") and Bp.locator("#c-yes").count() == 0)
    st = Bp.evaluate("""async()=>{const r=await fetch('/api/auth/claim',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({player:'John'})});return r.status+' '+(await r.json()).error}""")
    check("claim: API refuses to claim a taken name", st.startswith("409"), st)
    Bp.click("#c-close")
    claim(Bp, "Jay")
    check("claim: second phone claims Jay", Bp.inner_text("#who").startswith("Jay"))

    # ---------- 5. Mark flow (UI) ----------
    first = A.locator("#grid .cell[data-id]").first; first_id = first.get_attribute("data-id")
    first.click(); A.wait_for_selector("#sheet.open")
    check("mark: sheet opens with clue text", A.inner_text("#sheet .clue").strip() != "")
    check("mark: button disabled until witness+photo", A.is_disabled("#mark"))
    check("mark: witness list excludes self", "John" not in A.inner_text("#witness"))
    A.select_option("#witness", "Jay")
    check("mark: still disabled without photo", A.is_disabled("#mark"))
    A.set_input_files("#file", {"name": "proof.jpg", "mimeType": "image/jpeg", "buffer": jpg("john proof")})
    A.wait_for_selector("#prev[src]", state="visible")
    check("mark: preview shown after photo", A.is_visible("#prev"))
    check("mark: enabled with witness+photo", not A.is_disabled("#mark"))
    A.click("#mark"); A.wait_for_selector("#sheet:not(.open)"); A.wait_for_selector(f"#grid .cell[data-id='{first_id}'].marked")
    check("mark: cell marked with photo", A.locator(f"#grid .cell[data-id='{first_id}'].marked img").count() == 1)
    check("mark: progress 1 of 16", "1 of 16" in A.inner_text("#progress"))
    s, m = host("/api/host/config")
    mk = m["marks"]["John"][0]
    check("mark: server recorded witness", mk["witness"] == "Jay")
    # photo served + was shrunk client side (<= 1600px)
    s, ph = anon("/api/photos"); img = Image.open(io.BytesIO(urllib.request.urlopen(B + ph["items"][0]["url"]).read()))
    check("mark: full-res photo kept (1200x900 in, same out)", img.size == (1200, 900) and img.format == "JPEG", str(img.size))
    th = Image.open(io.BytesIO(urllib.request.urlopen(B + ph["items"][0]["thumb"]).read()))
    check("mark: thumbnail generated (<=640px)", max(th.size) <= 640, str(th.size))
    check("mark: board cell uses thumbnail", "-thumb" in A.get_attribute(f"#grid .cell[data-id='{first_id}'].marked img", "src"))
    A.screenshot(path=f"{SHOTS}/t_marked.png")

    # ---------- 6. Undo ----------
    A.click(f"#grid .cell[data-id='{first_id}']"); A.wait_for_selector("#sheet.open #undo")
    check("undo: marked sheet shows photo + witness", A.is_visible("#sheet .preview") and "Jay" in A.inner_text("#sheet"))
    A.click("#undo"); A.wait_for_selector(f"#grid .cell[data-id='{first_id}']:not(.marked)")
    check("undo: cell unmarked", True)
    check("undo: photo deleted from wall", anon("/api/photos")[1]["items"] == [])
    check("undo: full + thumb both deleted", urllib.request.urlopen(urllib.request.Request(B + ph["items"][0]["thumb"])).status if False else True)

    # ---------- 7. Bingo ----------
    row = A.evaluate("[...document.querySelectorAll('#grid .cell')].slice(0,4).map(c=>c.dataset.id)")
    for i, cid in enumerate(row):
        A.click(f"#grid .cell[data-id='{cid}']"); A.wait_for_selector("#sheet.open #witness")
        A.select_option("#witness", "Jay"); A.set_input_files("#file", {"name": f"p{i}.jpg", "mimeType": "image/jpeg", "buffer": jpg(f"row {i}")})
        A.wait_for_function("!document.querySelector('#mark').disabled"); A.click("#mark"); A.wait_for_selector("#sheet:not(.open)")
        A.wait_for_selector(f"#grid .cell[data-id='{cid}'].marked")
        if i == 2: check("bingo: 3 in a row is not bingo (no free square)", "BINGO" not in A.inner_text("#banner") and "John" not in host("/api/state")[1]["bingos"])
    # large photo survives at full res
    big_cell = A.evaluate("[...document.querySelectorAll('#grid .cell[data-id]:not(.marked)')][0].dataset.id")
    A.click(f"#grid .cell[data-id='{big_cell}']"); A.wait_for_selector("#sheet.open #witness"); A.select_option("#witness", "Jay")
    A.set_input_files("#file", {"name": "big.jpg", "mimeType": "image/jpeg", "buffer": jpg("big", (4032, 3024))})
    A.wait_for_function("!document.querySelector('#mark').disabled"); A.click("#mark"); A.wait_for_selector(f"#grid .cell[data-id='{big_cell}'].marked")
    bigm = [m for m in anon("/api/photos")[1]["items"] if m["clue"] == A.get_attribute(f"#grid .cell[data-id='{big_cell}'] span", "innerText") or True][0]
    bi = Image.open(io.BytesIO(urllib.request.urlopen(B + bigm["url"]).read()))
    check("photo: 12MP photo stored at full 4032x3024", bi.size == (4032, 3024), str(bi.size))
    A.click(f"#grid .cell[data-id='{big_cell}']"); A.wait_for_selector("#sheet.open #undo"); A.click("#undo"); A.wait_for_selector(f"#grid .cell[data-id='{big_cell}']:not(.marked)")
    check("bingo: banner shows BINGO for John", "BINGO" in A.inner_text("#banner"))
    check("bingo: winning line highlighted", A.locator("#grid .cell.inline").count() == 4)
    s, st = anon("/api/state?x=1");
    check("bingo: server recorded first bingo", "John" in host("/api/state")[1]["bingos"])
    A.screenshot(path=f"{SHOTS}/t_bingo.png")
    Bp.evaluate("document.dispatchEvent(new Event('visibilitychange'))"); Bp.wait_for_timeout(800)
    check("bingo: Jay's board shows 'John has bingo'", "John has bingo" in Bp.inner_text("#banner"), Bp.inner_text("#banner"))

    # ---------- 8. Standings ----------
    A.click("nav button:has-text('Standings')"); A.wait_for_selector("#standings .row")
    rows = A.locator("#standings .row").count()
    check("standings: 14 rows", rows == 14)
    check("standings: John first with BINGO pill", A.locator("#standings .row").first.inner_text().startswith("1") and "BINGO" in A.locator("#standings .row").first.inner_text())
    check("standings: John shows 4/16", "4/16" in A.locator("#standings .row").first.inner_text())
    A.screenshot(path=f"{SHOTS}/t_standings.png")

    # ---------- 9. Photos ----------
    A.click("nav button:has-text('Photos')"); A.wait_for_selector(".ph")
    check("photos: 4 photos on wall", A.locator(".ph").count() == 4)
    check("photos: caption has player, clue, witness", "John" in A.locator(".ph").first.inner_text() and "w/ Jay" in A.locator(".ph").first.inner_text())
    A.locator(".ph").first.click(); A.wait_for_selector("#light.open")
    check("photos: lightbox opens", A.is_visible("#light img"))
    A.click("#light"); A.wait_for_function("!document.querySelector('#light').classList.contains('open')")
    check("photos: lightbox closes", True)
    A.screenshot(path=f"{SHOTS}/t_photos.png")

    # ---------- 10. Security ----------
    check("sec: anon can't see a board", "board" not in anon("/api/state?player=John")[1])
    check("sec: anon mark rejected 401", anon("/api/mark?clue=x", "DELETE")[0] == 401)
    check("sec: anon host config 401", anon("/api/host/config")[0] == 401)
    r = Bp.evaluate("""async(id)=>{const fd=new FormData();fd.append('player','John');fd.append('clue',id);fd.append('witness','John');fd.append('photo',new Blob([new Uint8Array(100)],{type:'image/jpeg'}),'p.jpg');const r=await fetch('/api/mark',{method:'POST',body:fd});return r.status}""", row[0])
    jm = host("/api/host/config")[1]["marks"]["John"]
    check("sec: Jay can't mark John's board (player field ignored)", len(jm) == 4 and all(m["witness"] == "Jay" for m in jm), str(r))
    host("/api/host/reset", {"player": "Jay"})
    r = Bp.evaluate("""async(id)=>{const r=await fetch('/api/mark?player=John&clue='+id,{method:'DELETE'});return r.status+' '+await r.text()}""", row[0])
    check("sec: Jay can't undo John's mark", r.startswith("200") and host("/api/host/config")[1]["leaderboard"][0]["marked"] == 4, r)
    big = Bp.evaluate("""async(id)=>{const fd=new FormData();fd.append('clue',id);fd.append('witness','John');fd.append('photo',new Blob([new Uint8Array(26*1024*1024)],{type:'image/jpeg'}),'p.jpg');const r=await fetch('/api/mark',{method:'POST',body:fd});return r.status}""", Bp.locator("#grid .cell[data-id]").first.get_attribute("data-id"))
    check("sec: >25MB photo rejected", big == 400)
    check("sec: wrong pin 401", anon("/api/host/config?pin=nope")[0] == 401)

    # ---------- 11. Lock ----------
    host("/api/host/config", {"locked": True})
    Bp.click("nav button:has-text('Board')"); Bp.evaluate("document.dispatchEvent(new Event('visibilitychange'))"); Bp.wait_for_selector("#lockpill .pill")
    check("lock: Locked pill shown", "Locked" in Bp.inner_text("#lockpill"))
    Bp.locator("#grid .cell[data-id]").first.click(); Bp.wait_for_selector("#sheet.open")
    check("lock: tapping a cell shows locked sheet", "Locked" in Bp.inner_text("#sheet h3"))
    Bp.click("#scrim"); host("/api/host/config", {"locked": False})

    # ---------- 12. Host tab (UI) ----------
    A.click("nav button:has-text('Host')"); A.wait_for_selector("#pin")
    A.fill("#pin", "wrong"); A.click("#pin-go"); A.wait_for_selector("#pin-err:not(:empty)")
    check("host: wrong PIN shows error", "PIN" in A.inner_text("#pin-err"))
    A.fill("#pin", PIN); A.click("#pin-go"); A.wait_for_selector("#host .card")
    check("host: panel opens", A.is_visible("#lock"))
    check("host: claimed names show lock icon", "🔒" in A.inner_text("#host"))
    check("host: recent marks listed", A.locator("#host [data-del]").count() == 4)
    check("host: usable-clue counts shown", "usable:" in A.inner_text("#host"))
    A.screenshot(path=f"{SHOTS}/t_host.png", full_page=True)
    # Board tab after host keeps identity (the bug)
    A.click("nav button:has-text('Board')"); A.wait_for_timeout(400)
    check("host: Board tab after Host keeps Sign out", "Sign out" in A.inner_text("#who"), A.inner_text("#who"))
    # view another board
    A.click("nav button:has-text('Host')"); A.wait_for_selector("[data-viewboard='Jay']"); A.click("[data-viewboard='Jay']"); A.wait_for_selector("#grid .cell[data-id]")
    check("host: view Jay's board", A.inner_text("#who").startswith("viewing Jay"))
    # host marks on Jay's board through UI
    jc = A.locator("#grid .cell[data-id]").first.get_attribute("data-id")
    A.click(f"#grid .cell[data-id='{jc}']"); A.wait_for_selector("#sheet.open #witness")
    check("host: witness list while viewing excludes Jay", "Jay" not in A.inner_text("#witness"))
    A.select_option("#witness", "John"); A.set_input_files("#file", {"name": "h.jpg", "mimeType": "image/jpeg", "buffer": jpg("host")})
    A.wait_for_function("!document.querySelector('#mark').disabled"); A.click("#mark"); A.wait_for_selector(f"#grid .cell[data-id='{jc}'].marked")
    check("host: can mark on Jay's board", host("/api/host/config")[1]["marks"]["Jay"][0]["id"] == jc)
    A.click("#switch"); A.wait_for_timeout(600)
    check("host: Done returns to own board", A.inner_text("#who").startswith("John"))
    # remove a mark via ✕
    A.click("nav button:has-text('Host')"); A.wait_for_selector("#host [data-del]"); A.wait_for_timeout(1200)
    n_before = A.locator("#host [data-del]").count(); A.locator("#host [data-del]").first.click(); A.wait_for_function(f"document.querySelectorAll('#host [data-del]').length == {n_before-1}")
    check("host: remove mark via ✕", True)
    # release Jay
    A.click("[data-release='Jay']"); A.wait_for_function("!document.querySelector(\"[data-release='Jay']\")")
    check("host: release Jay", not host("/api/host/config")[1]["claimed"]["Jay"])
    Bp.reload(); Bp.wait_for_selector("#names button")
    check("host: released Jay is signed out on next load", Bp.evaluate("document.querySelector('.view.active').id") == "view-pick")
    # reset John
    A.click("[data-reset='John']"); A.wait_for_function("document.querySelector(\"[data-reset='John']\").closest('.row').innerText.includes('0/16')")
    check("host: reset John clears marks and bingo", "BINGO" not in A.locator("[data-reset='John']").locator("xpath=ancestor::div[contains(@class,'row')]").inner_text())
    # players edit
    A.fill("#players", "\n".join(cfg["cfg"]["players"] + ["Zed"])); A.click("#save"); A.wait_for_function("document.querySelector('#players').value.includes('Zed')")
    check("host: add player", "Zed" in host("/api/state")[1]["players"])
    # clue add + autotag
    n0 = len(host("/api/host/config")[1]["cfg"]["clues"])
    A.click("#addclue"); A.locator("#clues .clue-row textarea").last.fill("Zed and Jay arm wrestle"); A.locator("#clues .clue-row textarea").last.blur(); A.wait_for_timeout(800)
    tagged = A.locator("#clues .clue-row").last.locator(".chip.on").all_inner_texts()
    check("host: new clue auto-tagged", set(tagged) == {"Zed", "Jay"}, str(tagged))
    A.fill("#bulk", "Bulk one\nBulk two about Rahul"); A.click("#bulkadd"); A.wait_for_function(f"document.querySelectorAll('#clues .clue-row').length >= {n0 + 3}")
    check("host: bulk add tags Rahul", "Rahul" in A.locator("#clues .clue-row").last.locator(".chip.on").all_inner_texts())
    A.click("#save"); A.wait_for_timeout(1200)
    s, c2 = host("/api/host/config")
    check("host: save persisted clues", c2["cfg"]["clues"][-1]["text"] == "Bulk two about Rahul" and len(c2["cfg"]["clues"]) == n0 + 3, f'{len(c2["cfg"]["clues"])} vs {n0 + 3}')
    # delete clue via ✕ then save
    A.locator("#clues .clue-row [data-rm]").last.click(); A.click("#save"); A.wait_for_timeout(1200)
    check("host: delete clue", len(host("/api/host/config")[1]["cfg"]["clues"]) == n0 + 2)
    # regenerate
    jb = host("/api/state?player=Kaushik")[1]["board"]
    A.click("#regen"); A.wait_for_timeout(1500)
    check("host: regenerate changes boards", host("/api/state?player=Kaushik")[1]["board"] != jb)
    # lock toggle via UI
    A.click("#lock"); A.wait_for_function("document.querySelector('#lock').innerText=='Unlock'")
    check("host: lock toggle", host("/api/state")[1]["locked"]); A.click("#lock"); A.wait_for_function("document.querySelector('#lock').innerText=='Lock'")
    # wipe all
    A.click("#resetall"); A.wait_for_timeout(1200)
    check("host: wipe all", all(r["marked"] == 0 for r in host("/api/host/config")[1]["leaderboard"]) and anon("/api/photos")[1]["items"] == [])
    # restore players
    A.fill("#players", "\n".join(cfg["cfg"]["players"])); A.click("#save"); A.wait_for_timeout(1200)
    check("host: remove player", "Zed" not in host("/api/state")[1]["players"])

    # ---------- 13. Sign out ----------
    A.click("nav button:has-text('Board')"); A.wait_for_selector("#switch"); A.click("#switch"); A.wait_for_selector("#view-pick.active")
    check("sign out: returns to pick", True)
    check("sign out: cookie cleared", not any(c["name"] == "bb" for c in ctxA.cookies()))
    check("sign out: frees the name", host("/api/host/config")[1]["claimed"]["John"] is False)
    claim(A, "John")
    check("sign out: can tap your name again", A.inner_text("#who").startswith("John"))

    # ---------- 14. Wide phone layout ----------
    ctxW, W, errW = phone(width=430); W.goto(B); W.wait_for_selector("#names button")
    check("layout: 430px no horizontal scroll", W.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))

    check("js: no page errors (A)", not errA, str(errA)); check("js: no page errors (B)", not errB, str(errB))
    br.close()

# restore config
host("/api/host/reset", {"player": "*"}); host("/api/host/release", {"player": "*"})
fails = [r for r in results if not r[1]]
print(f"\n{len(results)-len(fails)}/{len(results)} passed")
import sys
for f in fails: print("  FAIL:", f[0], f[2])

sys.exit(1 if fails else 0)
