"""Szenen im Emulator Ardens spielen – mit dem echten AVR-Programm.

Gegenstück zum Host-Prüfstand (tests/fxhost): dieselben Befehle auf stdin,
dieselbe Spur auf stdout, nur läuft das gebaute Paket im Ardens-Web-Player
(headless Chromium über Playwright). Der Treiber drückt Tasten und liest den
Zustand des Spiels aus dem emulierten RAM: Die Adressen der Variablen stehen
in der ELF-Datei des Sketches (avr-nm), den RAM findet er im Speicher des
Emulators über den GameHeader, der Byte für Byte aus game.bin stammt.

Aufruf (eigene Python-Umgebung mit Playwright und Chromium):
    python tests/ardens_run.py <ardens-player-verzeichnis> < befehle
Befehle wie bei tests/fxhost/harness.cpp; „seed“ wird ignoriert (der
Zufallsgenerator des Geräts läuft für sich).
"""
import asyncio
import functools
import http.server
import re
import subprocess
import sys
import threading
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
ELF = ROOT / "build" / "PocketAdventureFX.ino.elf"
GAME = ROOT / "PocketAdventureFX" / "fxdata" / "game.bin"
PACKAGE = ROOT / "dist" / "PocketAdventureFX.arduboy"
HEADER_H = ROOT / "PocketAdventureFX" / "gamedata.h"

NONE8, NONE24 = 0xFF, 0xFFFFFF
WIDTH, HEIGHT, BAR_H = 128, 64, 8
OBJ_OWNED, OBJ_HIDDEN, OBJ_STATE = 0x80, 0x40, 0x3F
SUBPIXEL_SHIFT = 4
STRING_VAR, INT_VAR = 0x01, 0x03
KEYS = {"a": "a", "b": "s", "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight"}
BITS = {"a": 0x08, "b": 0x04, "up": 0x80, "down": 0x10, "left": 0x20, "right": 0x40}   # Arduboy-Tastenbits
FRAME = 1000 / 60

# Mitschreiben im Browser: nach jedem Animationsframe (also direkt hinter dem
# Emulator) die beobachteten Bytes vergleichen und Änderungen mit dem
# Bildzähler des Sketches festhalten. Python holt sie mit drain() ab – so
# entgeht kein kurzer Text, auch wenn die Abfrage aus Python stockt.
SAMPLER = """(cfg) => {
  const H = () => Module.HEAPU8;
  const read = (a, n) => Array.from(H().subarray(cfg.base + a, cfg.base + a + n));
  const fields = cfg.fields;
  let prev = null;
  window.__mi = {events: [], drain() { const e = this.events; this.events = []; return e; }};
  const sample = () => {
    const now = {};
    for (const [k, [a, n]] of Object.entries(fields)) now[k] = read(a, n);
    const fc = H()[cfg.base + cfg.frame] | (H()[cfg.base + cfg.frame + 1] << 8);
    if (prev) {
      const changed = Object.keys(fields).filter(k => now[k].some((v, i) => v !== prev[k][i]));
      if (changed.length) window.__mi.events.push({frame: fc, changed, now});
    } else {
      window.__mi.events.push({frame: fc, changed: Object.keys(fields), now});
    }
    prev = now;
    requestAnimationFrame(sample);
  };
  requestAnimationFrame(sample);
}"""
LIMIT_MS = 5 * 60 * 1000


def symbols():
    """Name → (Adresse im SRAM, Größe) aus der ELF-Datei."""
    nm = next((ROOT / "build" / "arduino").rglob("avr-nm"), None) or "avr-nm"
    out = subprocess.run([str(nm), "-C", "-S", str(ELF)], capture_output=True, text=True, check=True).stdout
    syms = {}
    for line in out.splitlines():
        m = re.match(r"([0-9a-f]+) ([0-9a-f]+) [bBdD] (.+)$", line)
        if m and int(m.group(1), 16) >= 0x800000:
            name = m.group(3).replace("(anonymous namespace)::", "")
            syms[name] = (int(m.group(1), 16) - 0x800000, int(m.group(2), 16))
    return syms


def constants():
    h = HEADER_H.read_text()
    return {k: int(v) for k, v in re.findall(r"constexpr uint8_t (\w+) = (\d+);", h)}


def structs():
    """Felder der Datensätze aus gamedata.h: Name → {feld: (offset, größe)}."""
    sizes = {"uint8_t": 1, "int8_t": 1, "uint16_t": 2, "int16_t": 2, "__uint24": 3}
    out = {}
    for name, body in re.findall(r"struct (\w+) \{(.*?)\};", HEADER_H.read_text(), re.S):
        off, fields = 0, {}
        for typ, field in re.findall(r"(\w+) (\w+);", body):
            fields[field] = (off, sizes[typ])
            off += sizes[typ]
        fields["_size"] = (off, 0)
        out[name] = fields
    return out


def le(b, off, n):
    return int.from_bytes(bytes(b[off:off + n]), "little")


class Game:
    """Statische Daten aus game.bin (wie die Engine sie liest)."""

    def __init__(self):
        self.data = GAME.read_bytes()
        self.st = structs()
        c = constants()
        self.menu_cols = 2
        self.verb_rows = (c["VERB_COUNT"] + self.menu_cols - 1) // self.menu_cols
        self.actor_count = c["ACTOR_COUNT"]
        self.object_count = c["OBJECT_COUNT"]

    def u(self, off, n):
        return le(self.data, off, n)

    def record(self, name, address):
        f = self.st[name]
        return {k: self.u(address + o, n) for k, (o, n) in f.items() if not k.startswith("_")}

    def size(self, name):
        return self.st[name]["_size"][0]

    def actor_rec(self, header, a):
        return self.record("ActorRec", header["actors"] + a * self.size("ActorRec"))

    def sprite_size(self, sprite):
        return int.from_bytes(self.data[sprite:sprite + 2], "big"), int.from_bytes(self.data[sprite + 2:sprite + 4], "big")

    def places(self, room_rec):
        n = self.size("PlaceRec")
        return [self.record("PlaceRec", room_rec["places"] + i * n) for i in range(room_rec["placeCount"])]

    def string(self, address, ram):
        """Wie World::readString, mit Platzhaltern aus dem RAM."""
        out = []
        while True:
            c = self.data[address]
            address += 1
            if c == 0:
                break
            if c == STRING_VAR:
                slot = self.data[address] - 1
                address += 1
                out.append(ram.string(slot))
            elif c == INT_VAR:
                var = self.data[address] - 1
                address += 1
                out.append(str(ram.var(var)))
            else:
                out.append(bytes([c]).decode("cp437"))
        return "".join(out)


class Ram:
    def __init__(self, raw, syms):
        self.raw, self.s = raw, syms

    def at(self, name, off=0, n=1):
        return le(self.raw, self.s[name][0] + off, n)

    def signed16(self, name):
        v = self.at(name, 0, 2)
        return v - 0x10000 if v & 0x8000 else v

    def record(self, struct, symbol):
        o = self.s[symbol][0]
        return {k: le(self.raw, o + off, n) for k, (off, n) in self.st[struct].items() if not k.startswith("_")}

    def header(self):
        return self.record("GameHeader", "World::header")

    def room_rec(self):
        return self.record("RoomRec", "World::roomRec")

    def actor(self, a):
        o = self.s["World::actors"][0] + a * (self.s["World::actors"][1] // self.n_actors)
        r = self.raw
        return {"x": le(r, o, 2) >> SUBPIXEL_SHIFT, "y": le(r, o + 2, 2) >> SUBPIXEL_SHIFT,
                "room": r[o + 13], "walking": r[o + 16], "look": r[o + 17]}

    def string(self, slot):
        size = self.s["World::strings"][1] // self.n_strings
        o = self.s["World::strings"][0] + slot * size
        b = bytes(self.raw[o:o + size]).split(b"\0")[0]
        return b.decode("cp437").replace("@", "")

    def var(self, i):
        return self.raw[self.s["World::vars"][0] + i]

    def text(self):
        o, n = self.s["text"]
        return bytes(self.raw[o:o + n]).split(b"\0")[0].decode("cp437")


class Driver:
    def __init__(self, page, game, syms, base):
        self.page, self.game, self.syms, self.base = page, game, syms, base
        c = constants()
        self.n_actors = c["ACTOR_COUNT"]
        self.n_strings = c.get("STRING_SLOTS", 1)
        self.last = None
        self.lines = []

    async def ram(self):
        raw = await self.page.evaluate("([a, n]) => Array.from(Module.HEAPU8.subarray(a, a + n))", [self.base, 0xB00])
        r = Ram(raw, self.syms)
        r.n_actors, r.n_strings, r.st = self.n_actors, self.n_strings, self.game.st
        return r

    def out(self, line):
        print(line, flush=True)

    def fields(self):
        """Was der Sampler im Browser beobachtet: Name → (Adresse, Länge)."""
        s = self.syms
        stride = s["World::actors"][1] // self.n_actors
        f = {"room": (s["World::room"][0], 1), "card": (s["cardImage"][0], 3), "track": (s["currentTrack"][0], 1),
             "textFrames": (s["textFrames"][0], 2), "text": s["text"], "actor": (s["textActor"][0], 1),
             "choice": (s["choiceActive"][0], 1), "choiceCount": (s["choiceCount"][0], 1),
             "choiceText": s["choiceText"], "objects": s["World::objects"],
             "actors": (s["World::actors"][0], stride * self.n_actors)}
        return {k: list(v) for k, v in f.items()}

    async def start_sampler(self):
        await self.page.evaluate(SAMPLER, {"base": self.base, "frame": self.syms["Arduboy2Base::frameCount"][0],
                                           "fields": self.fields()})

    async def report(self):
        """Änderungen seit dem letzten Aufruf in der Reihenfolge des Prüfstands
        ausgeben (Raum, Musik, Karte, Text, Optionen, Zustände, Figuren)."""
        events = await self.page.evaluate("() => window.__mi.drain()")
        stride = self.syms["World::actors"][1] // self.n_actors
        for e in events:
            n = e["now"]
            st = self.state = getattr(self, "state", None) or {}
            room = n["room"][0]
            if room != st.get("room"):
                self.out(f"room {room}")
            track = n["track"][0]
            if track != st.get("track", NONE8) and track != NONE8:
                self.out(f"music {track}")
            card = le(n["card"], 0, 3) != NONE24
            if card and not st.get("card"):
                self.out("card")
            text = bytes(n["text"]).split(b"\0")[0].decode("cp437") if le(n["textFrames"], 0, 2) else ""
            if text and text != st.get("text", ""):
                who = "-" if n["actor"][0] == NONE8 else n["actor"][0]
                self.out(f"say {who} {text.replace(chr(10), ' ')}")
            choice = n["choice"][0]
            if choice and not st.get("choice"):
                r = await self.ram()
                for i in range(n["choiceCount"][0]):
                    self.out(f"option {i} {self.game.string(le(n['choiceText'], 3 * i, 3), r).replace(chr(10), ' ')}")
            states = [v & OBJ_STATE for v in n["objects"]]
            for o, (old, new) in enumerate(zip(st.get("states", [0] * len(states)), states)):
                if old != new:
                    self.out(f"state {o} {new}")
            rooms = [n["actors"][a * stride + 13] for a in range(self.n_actors)]
            for a in range(1, self.n_actors):
                if rooms[a] != st.get("rooms", [0] * self.n_actors)[a]:
                    if rooms[a] == NONE8:
                        self.out(f"gone {a}")
                    else:
                        x = le(n["actors"], a * stride, 2) >> SUBPIXEL_SHIFT
                        y = le(n["actors"], a * stride + 2, 2) >> SUBPIXEL_SHIFT
                        self.out(f"here {a} {x} {y}")
            self.state = {"room": room, "track": track, "card": card, "text": text, "choice": choice,
                          "states": states, "rooms": rooms}
        return await self.ram()

    async def frame(self):
        return await self.page.evaluate("(a) => Module.HEAPU8[a] | (Module.HEAPU8[a + 1] << 8)",
                                        self.base + self.syms["Arduboy2Base::frameCount"][0])

    async def frames(self, n):
        """n Bilder des Spiels abwarten (nicht Millisekunden: der Browser darf stocken)."""
        start = await self.frame()
        while (await self.frame() - start) & 0xFFFF < n:
            await self.report()
            await self.page.wait_for_timeout(5)

    async def wait(self, ms):
        await self.frames(max(1, round(ms / FRAME)))

    async def buttons(self):
        return await self.page.evaluate("(a) => Module.HEAPU8[a]", self.base + self.syms["Arduboy2Base::currentButtonState"][0])

    async def tap(self, key):
        """Taste drücken, bis das Spiel sie gesehen und einen Frame lang verarbeitet
        hat, dann loslassen und warten, bis es auch das gesehen hat."""
        await self.page.keyboard.down(KEYS[key])
        for _ in range(500):
            if await self.buttons() & BITS[key]:
                break
            await self.page.wait_for_timeout(2)
        await self.frames(1)
        await self.page.keyboard.up(KEYS[key])
        for _ in range(500):
            if not await self.buttons() & BITS[key]:
                break
            await self.page.wait_for_timeout(2)
        await self.frames(1)

    def fail(self, why):
        self.out(f"FEHLER {why}")
        raise SystemExit(1)

    def busy(self, r):
        fg = r.at("fg", 0, 3)
        return fg != NONE24 or r.at("textFrames", 0, 2) or r.at("choiceActive") or \
            r.at("cardImage", 0, 3) != NONE24 or r.at("pending")

    async def until(self, cond, what):
        t = 0
        while True:
            r = await self.report()
            if cond(r):
                return r
            t += 10
            if t > LIMIT_MS:
                self.fail(f"{what}: läuft nicht aus")
            await self.page.wait_for_timeout(10)

    async def idle(self):
        await self.until(lambda r: not self.busy(r), "idle")

    # --- Cursor, Menü, Objekte (wie harness.cpp) ---

    async def move_cursor(self, x, y):
        for _ in range(400):
            r = await self.report()
            cx, cy = r.signed16("cursorX"), r.signed16("cursorY")
            dx, dy = x - cx, y - cy
            if not dx and not dy:
                return
            keys = [k for k, on in (("right", dx > 0), ("left", dx < 0), ("down", dy > 0), ("up", dy < 0)) if on]
            if max(abs(dx), abs(dy)) > 8:
                for k in keys:
                    await self.page.keyboard.down(KEYS[k])
                await self.frames(3)
                for k in keys:
                    await self.page.keyboard.up(KEYS[k])
                await self.frames(1)
            else:
                await self.tap(keys[0])
        self.fail("Cursor erreicht das Ziel nicht")

    async def choose_verb(self, v):
        await self.idle()
        await self.tap("b")
        r = await self.report()
        if not r.at("menuOpen"):
            self.fail("Menü geht nicht auf")
        cols = self.game.menu_cols
        for _ in range(40):
            r = await self.report()
            row, col = r.at("menuRow"), r.at("menuCol")
            if (row, col) == (v // cols, v % cols):
                break
            await self.tap("up" if row > v // cols else "down" if row < v // cols else "left" if col > v % cols else "right")
        else:
            self.fail("Verb im Menü nicht erreichbar")
        await self.tap("a")

    def hit_test(self, r, x, y):
        g = self.game
        header, room = r.header(), r.at("World::room")
        for a in range(self.n_actors):
            act = r.actor(a)
            if act["room"] != room:
                continue
            rec = g.actor_rec(header, a)
            obj = rec["object"]
            if obj == NONE8 or r.at("World::objects", obj) & (OBJ_OWNED | OBJ_HIDDEN):
                continue
            w, h = g.sprite_size(g.actor_rec(header, act["look"])["sprite"])
            left = act["x"] - w // 2
            if left <= x < left + w and act["y"] - h < y <= act["y"]:
                return obj
        for p in reversed(g.places(r.room_rec())):
            if p["object"] == NONE8 or r.at("World::objects", p["object"]) & (OBJ_OWNED | OBJ_HIDDEN):
                continue
            if p["x"] <= x < p["x"] + p["w"] and p["y"] <= y < p["y"] + p["h"]:
                return p["object"]
        return NONE8

    def find_place(self, r, obj):
        return next((p for p in self.game.places(r.room_rec()) if p["object"] == obj), None)

    def visible_point(self, r, obj):
        p = self.find_place(r, obj)
        if not p:
            return None
        scroll, scroll_y = r.signed16("World::scrollX"), r.signed16("World::scrollY")

        def spread(center, n):
            yield center
            for d in range(1, n + 1):
                yield center - d
                yield center + d
        for y in spread(p["y"] + p["h"] // 2, p["h"]):
            if not BAR_H <= y - scroll_y < HEIGHT:
                continue
            for x in spread(p["x"] + p["w"] // 2, p["w"]):
                if 0 <= x - scroll < WIDTH and self.hit_test(r, x, y) == obj:
                    return x - scroll, y - scroll_y
        return None

    async def click(self, sx, sy):
        await self.move_cursor(sx, sy)
        await self.tap("a")

    async def walk(self, x, y):
        await self.idle()
        for _ in range(12):
            r = await self.report()
            sx, sy = r.signed16("World::scrollX"), r.signed16("World::scrollY")
            s, t = x - sx, y - sy
            cs = min(max(s, 0), WIDTH - 1)
            cy = min(max(t, BAR_H), HEIGHT - 1)

            def hit(px, py):
                return self.hit_test(r, px + sx, py + sy)
            d = 1
            while hit(cs, cy) != NONE8 and d < HEIGHT:
                u = cy + (d + 1) // 2 if d & 1 else cy - d // 2
                if BAR_H <= u < HEIGHT and hit(cs, u) == NONE8:
                    cy = u
                d += 1
            d = 1
            while hit(cs, cy) != NONE8 and d < 40:
                cs = cs + 1 if s < cs else cs - 1
                d += 1
            await self.click(cs, cy)
            await self.until(lambda r: not self.busy(r) and not r.actor(0)["walking"], "walk")
            if cs == s and cy == t:
                return

    async def aim(self, obj):
        r = await self.report()
        pt = self.visible_point(r, obj)
        if not pt:
            self.fail(f"Objekt {obj} nicht sichtbar")
        await self.move_cursor(*pt)

    async def do(self, v, obj):
        await self.choose_verb(v)
        for n in range(6):
            r = await self.report()
            pt = self.visible_point(r, obj)
            if pt:
                await self.click(*pt)
                return
            p = self.find_place(r, obj)
            if not p:
                self.fail(f"Objekt {obj} nicht im Raum")
            if p["walkX"] == 0xFFFF:
                await self.walk(p["x"] + p["w"] // 2, p["y"] + p["h"])
            else:
                await self.walk(p["walkX"], p["walkY"])
            await self.choose_verb(v)
        self.fail(f"Objekt {obj} nicht erreichbar")

    async def inventory(self, obj):
        r = await self.report()
        inv = [r.at("World::inventory", i) for i in range(r.at("World::inventoryCount"))]
        if obj not in inv:
            self.fail(f"Gegenstand {obj} nicht im Inventar")
        i = inv.index(obj)
        await self.tap("b")
        row, col = self.game.verb_rows + i // self.game.menu_cols, i % self.game.menu_cols
        for _ in range(60):
            r = await self.report()
            if (r.at("menuRow"), r.at("menuCol")) == (row, col):
                break
            mr, mc = r.at("menuRow"), r.at("menuCol")
            await self.tap("down" if mr < row else "up" if mr > row else "right" if mc < col else "left")
        await self.tap("a")

    async def choose(self, n, confirm):
        r = await self.until(lambda r: r.at("choiceActive"), "choose: kein Dialog")
        if n >= r.at("choiceCount"):
            self.fail(f"choose: nur {r.at('choiceCount')} Optionen")
        while True:
            r = await self.report()
            sel = r.at("choiceSel")
            if sel == n:
                break
            await self.tap("up" if sel > n else "down")
        if confirm:
            await self.tap("a")

    async def run(self, lines):
        for line in lines:
            toks = line.split()
            if not toks or toks[0].startswith("#"):
                continue
            self.out(f"> {line}")
            cmd, *a = toks
            n = [int(x, 0) if re.fullmatch(r"-?\d+", x) else x for x in a]
            if cmd == "lang":
                await self.until(lambda r: r.at("mode") == 0, "Sprachauswahl")
                while True:
                    r = await self.report()
                    if r.at("language") == n[0]:
                        break
                    await self.tap("down" if r.at("language") < n[0] else "up")
                await self.tap("a")
            elif cmd == "start":
                await self.until(lambda r: r.at("mode") == 1, "Titelbild")
                await self.tap("a")
            elif cmd == "idle":
                await self.idle()
            elif cmd == "frames":
                await self.frames(n[0])
            elif cmd in ("choose", "select"):
                await self.choose(n[0], cmd == "choose")
            elif cmd == "do":
                await self.do(n[0], n[1])
            elif cmd == "verb":
                await self.choose_verb(n[0])
            elif cmd == "inv":
                await self.choose_verb(n[0])
                await self.inventory(n[1])
            elif cmd == "aim":
                await self.aim(n[0])
            elif cmd == "click":
                await self.tap("a")
            elif cmd == "press":
                await self.tap(a[0])
            elif cmd == "walk":
                await self.walk(n[0], n[1])
            elif cmd == "until":
                actor, rel, x = n[0], a[1], n[2]
                await self.until(lambda r: r.actor(actor)["room"] == r.at("World::room") and
                                 (r.actor(actor)["x"] < x if rel == "<" else r.actor(actor)["x"] > x), "until")
            elif cmd == "pos":
                r = await self.report()
                act = r.actor(n[0])
                self.out(f"pos {n[0]} {act['x']} {act['y']} room {act['room']}")
            elif cmd == "shot":
                await self.page.locator("#canvas").screenshot(path=a[0])
            elif cmd == "seed":
                pass
            else:
                self.fail(f"unbekannter Befehl {cmd}")


async def main(player_dir):
    syms = symbols()
    game = Game()
    lines = sys.stdin.read().splitlines()

    # Player und Paket über einen eigenen kleinen Webserver ausliefern.
    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            if path.split("?")[0].endswith(PACKAGE.name):
                return str(PACKAGE)
            return super().translate_path(path)

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=player_dir))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    count = game.data[4]
    heads = []
    for i in range(count):
        h = le(game.data, 5 + i * 6 + 3, 3)
        heads.append(list(game.data[h:h + 40]))

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 512, "height": 256})
        await page.goto(f"{url}/player.html?x=1")
        await page.wait_for_function("() => window.Module && Module.calledRun")
        await page.evaluate("""async (f) => {
            const buf = new Uint8Array(await (await fetch(f + '?' + Date.now())).arrayBuffer());
            loadFile('file', f, buf);}""", PACKAGE.name)
        await page.click("#canvas")
        header_addr = syms["World::header"][0]
        # Der GameHeader steht auch in den Kopien des FX-Flashs; der SRAM ist
        # die Fundstelle, an der der Stapelzeiger (SPL/SPH an 0x5D/0x5E des
        # Datenraums) in den SRAM zeigt. Er ist erst nach setup() gefüllt.
        base = None
        for _ in range(600):   # bis 60 s, auch wenn der Rechner ausgelastet ist
            found = await page.evaluate("""(heads) => {
                const h = Module.HEAPU8, out = [];
                for (const pat of heads)
                  for (let i = 0; i + pat.length < h.length; i++) {
                    if (h[i] !== pat[0]) continue;
                    let k = 1; while (k < pat.length && h[i + k] === pat[k]) k++;
                    if (k === pat.length) out.push(i);
                  }
                return out;}""", heads)
            for f in sorted(found):
                b = f - header_addr
                sp = await page.evaluate("(a) => Module.HEAPU8[a] | (Module.HEAPU8[a + 1] << 8)", b + 0x5D)
                if 0x100 <= sp < 0xB00:
                    base = b
                    break
            if base is not None:
                break
            await page.wait_for_timeout(100)
        if base is None:
            print("FEHLER RAM des Emulators nicht gefunden", flush=True)
            return 1
        drv = Driver(page, game, syms, base)
        await drv.start_sampler()
        try:
            await drv.run(lines)
        except SystemExit as e:
            await browser.close()
            return e.code
        r = await drv.report()
        await browser.close()
    print("ende", flush=True)
    return 0


if __name__ == "__main__":
    # Ausgabe wie beim Host-Prüfstand in CP437 (dem Zeichensatz der Spieltexte)
    sys.stdout.reconfigure(encoding="cp437")
    sys.exit(asyncio.run(main(sys.argv[1])))
