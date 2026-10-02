#!/usr/bin/env python3
"""Liest Raumhintergründe aus SCUMM-v4-Spieldaten (Monkey Island 1, VGA-Disketten).

Die Originaldateien (DISK01.LEC …) bleiben, wo sie sind; dieses Modul liest sie
nur. Extrahierte Bilder gehören nicht ins Repo (siehe .gitignore), sie werden
beim Build aus der eigenen Kopie erzeugt.

Format (nach ScummVM, engines/scumm: resource_v4.cpp, room.cpp, gfx.cpp):
  * Dateien sind mit 0x69 XOR-verschlüsselt.
  * Blöcke: u32 Größe (inkl. Kopf, little-endian) + 2-Zeichen-Tag.
    LE (Datei) → LF (Raum, danach u16 Raumnummer) → RO → HD, PA, BM, …
  * HD: u16 Breite, u16 Höhe, u16 Objektanzahl.
  * PA: u16 Bytezahl, danach RGB-Tripel.
  * BM: u32 Länge, danach je 8-px-Streifen ein u32-Offset (relativ zum
    Datenanfang), an dem der Streifen mit einem Codec-Byte beginnt.
  * BX: u8 Anzahl, danach je Box 4 Eckpunkte (s16 x, y: ul, ur, lr, ll),
    u8 Maske, u8 Flags, u16 Skalierung (boxes.cpp, „old“-Format).
  * OC (Objektcode, Offsets ab Blockanfang): u16 Nummer @6, x/8 @9,
    y/8 @10 (Bit 7: Elternzustand), Breite/8 @11, u16 Laufziel x @13 und
    y @15, @17: Blickrichtung (Bits 0–2) und Höhe (Bits 3–7),
    Namens-Offset @18 (object.cpp: ScummEngine_v4::resetRoomObject).

Aufruf als Skript: listet Räume bzw. schreibt sie als PNG.
"""
import argparse
import struct
import sys
from pathlib import Path

from PIL import Image

XOR_KEY = 0x69


class ScummError(Exception):
    pass


def blocks(data, start, end):
    """Iteriert über (tag, datenanfang, blockende) zwischen start und end."""
    off = start
    while off + 6 <= end:
        size, = struct.unpack_from("<I", data, off)
        tag = data[off + 4:off + 6].decode("latin1")
        if size < 6 or off + size > end:
            raise ScummError(f"kaputter Block {tag!r} bei {off:#x} (Größe {size})")
        yield tag, off + 6, off + size
        off += size


def find(data, start, end, tag):
    for t, s, e in blocks(data, start, end):
        if t == tag:
            return s, e
    return None


class Box:
    """Begehbares Viereck; Ecken im Uhrzeigersinn ab oben links."""

    def __init__(self, corners, flags, scale=255):
        self.corners = corners  # [(x, y)] × 4: ul, ur, lr, ll
        self.flags = flags
        self.scale = scale      # Figurengröße in dieser Box, 255 = volle Größe; 0x8000|n: Stufe n

    def bounds(self):
        xs = [x for x, _ in self.corners]
        ys = [y for _, y in self.corners]
        return min(xs), min(ys), max(xs), max(ys)


class RoomObject:
    def __init__(self, number, name, x, y, width, height, walk_x, walk_y, direction):
        self.number = number
        self.name = name
        self.x, self.y, self.width, self.height = x, y, width, height
        self.walk_x, self.walk_y = walk_x, walk_y
        self.direction = direction


class Room:
    def __init__(self, number, width, height, palette, bitmap, boxes=(), objects=(), object_images=None):
        self.number = number
        self.width = width
        self.height = height
        self.palette = palette  # Liste von (r, g, b), 0–255
        self._bitmap = bitmap   # BM-Daten (ohne Blockkopf)
        self.boxes = list(boxes)
        self.objects = list(objects)
        self._object_images = object_images or {}  # Objektnummer → OI-Daten ab Streifentabelle

    def _decode(self, bm, width, height):
        pixels = bytearray(width * height)
        for strip in range(width // 8):
            offset, = struct.unpack_from("<I", bm, 4 + strip * 4)
            decode_strip(bm, offset, pixels, strip * 8, width, height)
        img = Image.frombytes("P", (width, height), bytes(pixels))
        flat = [c for rgb in self.palette for c in rgb]
        img.putpalette(flat + [0] * (768 - len(flat)))
        return img.convert("RGB")

    def object_image(self, number):
        """Bild eines Raumobjekts (erster Zustand) in dessen Größe laut OC."""
        obj = next((o for o in self.objects if o.number == number), None)
        if obj is None or number not in self._object_images:
            raise ScummError(f"Raum {self.number}: kein Bild für Objekt {number}")
        return self._decode(self._object_images[number], obj.width, obj.height)

    def image(self):
        """Dekodiert den Hintergrund als RGB-Bild."""
        if len(self._bitmap) < 4 + 4 * (self.width // 8):
            raise ScummError(f"Raum {self.number} hat kein Hintergrundbild")
        return self._decode(self._bitmap, self.width, self.height)


def read_rooms(game_dir):
    """Alle Räume aus allen DISK*.LEC eines Spielverzeichnisses."""
    rooms = {}
    files = sorted(Path(game_dir).glob("DISK*.LEC"), key=lambda p: p.name.upper())
    if not files:
        raise ScummError(f"keine DISK*.LEC in {game_dir}")
    for path in files:
        data = bytes(b ^ XOR_KEY for b in path.read_bytes())
        for tag, s, e in blocks(data, 0, len(data)):
            if tag != "LE":
                raise ScummError(f"{path.name}: erwarte LE-Block, nicht {tag!r}")
            for t, ls, le in blocks(data, s, e):
                if t != "LF":
                    continue
                number, = struct.unpack_from("<H", data, ls)
                ro = find(data, ls + 2, le, "RO")
                if not ro:
                    continue
                hd, pa, bm = (find(data, *ro, tag) for tag in ("HD", "PA", "BM"))
                if not (hd and pa and bm):
                    continue
                width, height = struct.unpack_from("<HH", data, hd[0])
                count, = struct.unpack_from("<H", data, pa[0])
                raw = data[pa[0] + 2:pa[0] + 2 + count]
                palette = [tuple(raw[i:i + 3]) for i in range(0, len(raw) - 2, 3)]
                room = Room(number, width, height, palette, data[bm[0]:bm[1]],
                            read_boxes(data, find(data, *ro, "BX")),
                            read_objects(data, ro), read_object_images(data, ro))
                # Skalierungsstufen (SA, ScummVM: SCAL): 4 × (Größe1, y1, Größe2, y2);
                # eine Box mit Skalierung 0x8000 | n nimmt Stufe n (Größe nach y)
                sa = find(data, *ro, "SA")
                raw = data[sa[0]:sa[0] + 32] if sa else bytes(32)
                room.scale_slots = [struct.unpack_from("<4h", raw, 8 * i) for i in range(4)]
                rooms[number] = room
    return rooms


def read_boxes(data, bx):
    if not bx:
        return []
    start, end = bx
    count = data[start]
    boxes = []
    for i in range(count):
        off = start + 1 + i * 20
        if off + 20 > end:
            raise ScummError("BX-Block kürzer als angegeben")
        v = struct.unpack_from("<8hBBH", data, off)
        corners = [(v[0], v[1]), (v[2], v[3]), (v[4], v[5]), (v[6], v[7])]
        boxes.append(Box(corners, v[9], v[10]))
    return boxes


def read_object_images(data, ro):
    """OI-Blöcke: u16 Objektnummer, danach Streifentabelle wie BM (object.cpp:
    getObjectImage überspringt bei kleinen Headern 8 Bytes = Kopf + Nummer)."""
    images = {}
    for tag, s, e in blocks(data, *ro):
        if tag == "OI" and e - s > 2:
            number, = struct.unpack_from("<H", data, s)
            images[number] = data[s + 2:e]
    return images


def read_objects(data, ro):
    objects = []
    for tag, s, e in blocks(data, *ro):
        if tag != "OC":
            continue
        b = s - 6  # Offsets in ScummVM zählen ab Blockanfang
        number, = struct.unpack_from("<H", data, b + 6)
        walk_x, walk_y = struct.unpack_from("<HH", data, b + 13)
        name_at = b + data[b + 18]
        name = data[name_at:data.index(0, name_at)].decode("latin1")
        objects.append(RoomObject(
            number, name,
            x=data[b + 9] * 8, y=(data[b + 10] & 0x7F) * 8,
            width=data[b + 11] * 8, height=data[b + 17] & 0xF8,
            walk_x=walk_x, walk_y=walk_y, direction=data[b + 17] & 7))
    return objects


# --------------------------------------------------------------------------
# Kostüme (Figuren): Index 000.LFL → CO-Blöcke, Renderer nach costume.cpp
# --------------------------------------------------------------------------
#
# Ein Kostüm besteht aus bis zu 16 Einzelteilen („Limbs“, z. B. Körper und
# Kopf). Eine Animation (Nummer = Richtung + 4 × Aktion) setzt für jedes Limb
# einen Bereich der Befehlsliste; jeder Befehl wählt ein Bild dieses Limbs.
# Standardaktionen (actor.cpp): 1 Init, 2 Laufen, 3 Stehen, 4 Reden an,
# 5 Reden aus. Richtungen: 0 links, 1 rechts, 2 vorne, 3 hinten.

INIT, WALK, STAND, TALK_START, TALK_STOP = 1, 2, 3, 4, 5
LEFT, RIGHT, FRONT, BACK = 0, 1, 2, 3


def read_costume(game_dir, number):
    """CO-Block eines Kostüms über das Inhaltsverzeichnis 000.LFL.

    Die Offsets im Verzeichnis 0C zählen ab Raumblock (LF) + 8, also hinter
    Blockkopf und Raumnummer; wo der Raumblock liegt, steht im FO-Block der
    jeweiligen Diskdatei. Sequentielles Ablaufen geht nicht: Die SO-Blöcke
    dieser Version tragen falsche Größen.
    """
    game_dir = Path(game_dir)
    index = (game_dir / "000.LFL").read_bytes()
    costumes = None
    for tag, s, e in blocks(index, 0, len(index)):
        if tag == "0C":
            count, = struct.unpack_from("<H", index, s)
            costumes = [struct.unpack_from("<BI", index, s + 2 + i * 5) for i in range(count)]
    if costumes is None:
        raise ScummError("000.LFL ohne Kostümverzeichnis (0C)")
    if not 0 <= number < len(costumes) or not costumes[number][0]:
        raise ScummError(f"Kostüm {number} gibt es nicht")
    room, offset = costumes[number]
    for path in sorted(game_dir.glob("DISK*.LEC"), key=lambda p: p.name.upper()):
        data = bytes(b ^ XOR_KEY for b in path.read_bytes())
        for i in range(data[12]):  # FO-Block direkt nach dem LE-Kopf
            r, lf = struct.unpack_from("<BI", data, 13 + i * 5)
            if r == room and data[lf + 4:lf + 6] == b"LF":
                at = lf + 8 + offset
                size, = struct.unpack_from("<I", data, at)
                if data[at + 4:at + 6] != b"CO":
                    raise ScummError(f"Kostüm {number}: kein CO-Block an {at:#x}")
                return Costume(number, data[at:at + size])
    raise ScummError(f"Kostüm {number}: Raum {room} in keiner Diskdatei")


def _resource_block(game_dir, directory, number, tag):
    """Block einer Ressource über das Inhaltsverzeichnis 000.LFL (0C, 0N …).
    Offsets zählen ab Raumblock + 8 (siehe read_costume)."""
    game_dir = Path(game_dir)
    index = (game_dir / "000.LFL").read_bytes()
    entries = None
    for t, s, e in blocks(index, 0, len(index)):
        if t == directory:
            count, = struct.unpack_from("<H", index, s)
            entries = [struct.unpack_from("<BI", index, s + 2 + i * 5) for i in range(count)]
    if entries is None:
        raise ScummError(f"000.LFL ohne Verzeichnis {directory}")
    if not 0 <= number < len(entries) or not entries[number][0]:
        raise ScummError(f"{tag} {number} gibt es nicht")
    room, offset = entries[number]
    for path in sorted(game_dir.glob("DISK*.LEC"), key=lambda p: p.name.upper()):
        data = bytes(b ^ XOR_KEY for b in path.read_bytes())
        for i in range(data[12]):  # FO-Block direkt nach dem LE-Kopf
            r, lf = struct.unpack_from("<BI", data, 13 + i * 5)
            if r == room and data[lf + 4:lf + 6] == b"LF":
                at = lf + 8 + offset
                size, = struct.unpack_from("<I", data, at)
                if data[at + 4:at + 6].decode("latin1") != tag:
                    raise ScummError(f"{tag} {number}: kein {tag}-Block an {at:#x}")
                return data[at:at + size]
    raise ScummError(f"{tag} {number}: Raum {room} in keiner Diskdatei")


def read_sound(game_dir, number):
    """Sound-Ressource: {"WA": PC-Speaker-Daten, "AD": AdLib-Daten} (je ohne
    Blockkopf). MI1 verschachtelt SO-Blöcke; genommen wird jeweils der erste
    WA- und AD-Block (sound.cpp: readSoundResource)."""
    data = _resource_block(game_dir, "0N", number, "SO")
    found = {}

    def walk(at, end):
        while at + 6 <= end:
            size, = struct.unpack_from("<I", data, at)
            tag = data[at + 4:at + 6].decode("latin1")
            if size < 6:
                break
            if tag == "SO":
                walk(at + 6, min(at + size, end))
            elif tag in ("WA", "AD") and tag not in found:
                found[tag] = data[at + 6:at + size]
            at += size

    walk(6, len(data))
    return found


def adlib_melody(ad, channel=None):
    """AdLib-Musik (AD) → einstimmige Melodie als [(Hz, ms)], Schleifen-Flag
    und gewählter MIDI-Kanal.

    Format nach sound.cpp (convertADResource): 2 Bytes, 0x80 = Musik, Ticks
    (Tempo), play_once, …, 8 Instrumente à 16 Bytes, dann eine MIDI-Spur mit
    480 Ticks pro Viertel und 500000·256/Ticks µs pro Viertel; die Spur
    beginnt ohne Zeitangabe mit dem ersten Ereignis.

    Der PC-Speaker ist einstimmig, also bleibt nur die Melodiestimme: der
    Kanal mit der höchsten mittleren Tonlage, ohne Kanäle, die immer dieselbe
    Tonhöhe spielen (Schlagzeug auf AdLib-Melodiekanälen). channel legt den
    Kanal fest, falls die Automatik danebenliegt.
    """
    if len(ad) < 0x13 + 128 or ad[2] != 0x80:
        raise ScummError("AD-Ressource ist keine Musik")
    ticks, play_once = ad[3], ad[4]
    track = ad[2 + 0x11 + 128:]
    us_per_tick = 500000 * 256 / ticks / 480

    events = []  # (tick, an?, kanal, note)
    pos = tick = 0
    status = 0

    def vlq():
        nonlocal pos
        value = 0
        while True:
            b = track[pos]
            pos += 1
            value = (value << 7) | (b & 0x7F)
            if not b & 0x80:
                return value

    first = True
    while pos < len(track):
        if not first:
            tick += vlq()
        first = False
        b = track[pos]
        if b & 0x80:
            status = b
            pos += 1
        kind, ch = status & 0xF0, status & 0x0F
        if status == 0xFF:
            meta = track[pos]
            pos += 1
            pos += vlq()
            if meta == 0x2F:
                break
        elif status in (0xF0, 0xF7):
            pos += vlq()
        elif kind in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
            note, velocity = track[pos], track[pos + 1]
            pos += 2
            if kind == 0x90 and velocity:
                events.append((tick, 1, ch, note))
            elif kind in (0x80, 0x90):
                events.append((tick, 0, ch, note))
        elif kind in (0xC0, 0xD0):
            pos += 1
        else:
            raise ScummError(f"unbekanntes MIDI-Ereignis {status:#x}")
    end_tick = tick

    if channel is None:
        pitches = {}
        for _, on, ch, note in events:
            if on:
                pitches.setdefault(ch, []).append(note)
        melodic = {ch: sum(p) / len(p) for ch, p in pitches.items() if len(set(p)) > 1}
        if not melodic:
            raise ScummError("keine Melodiestimme gefunden")
        channel = max(melodic, key=melodic.get)

    active = {}
    melody = []  # (start_tick, midi_note|None)
    for t, on, ch, note in sorted(events, key=lambda e: (e[0], e[1])):
        if ch != channel:
            continue
        if on:
            active[note] = active.get(note, 0) + 1
        elif active.get(note):
            active[note] -= 1
            if not active[note]:
                del active[note]
        top = max(active, default=None)
        if melody and melody[-1][0] == t:
            melody[-1] = (t, top)
        elif not melody or melody[-1][1] != top:
            melody.append((t, top))
    notes = []
    for i, (t, note) in enumerate(melody):
        t_next = melody[i + 1][0] if i + 1 < len(melody) else end_tick
        ms = round((t_next - t) * us_per_tick / 1000)
        if ms <= 0:
            continue
        hz = 0 if note is None else round(440 * 2 ** ((note - 69) / 12))
        notes.append((hz, ms))
    # Die Stille bis zum ersten Ton der Stimme gehört zum Stück (Auftakt).
    first_tick = melody[0][0] if melody else 0
    if first_tick:
        notes.insert(0, (0, round(first_tick * us_per_tick / 1000)))
    return notes, not play_once, channel


class Costume:
    def __init__(self, number, data):
        self.number = number
        self.data = data
        self.num_anims = data[6]
        fmt = data[7] & 0x7F
        if fmt not in (0x58, 0x59):
            raise ScummError(f"Kostüm {number}: Format {fmt:#x} nicht unterstützt")
        self.colors = 16 if fmt == 0x58 else 32
        self.palette = data[8:8 + self.colors]  # Indizes in die Raumpalette
        p = 8 + self.colors
        self.anim_cmds = struct.unpack_from("<H", data, p)[0]
        self.frame_offsets = p + 2
        self.anim_offsets = p + 34

    def _u16(self, at):
        return struct.unpack_from("<H", self.data, at)[0]

    def _s16(self, at):
        return struct.unpack_from("<h", self.data, at)[0]

    def new_state(self):
        return {"pos": [0xFFFF] * 16, "start": [0] * 16, "end": [0] * 16,
                "noloop": [False] * 16, "stopped": 0}

    def apply(self, state, action, direction):
        """costumeDecodeData: setzt die Limbs für Animation direction + 4·action."""
        anim = direction + action * 4
        if anim > self.num_anims:
            return False
        r = self._u16(self.anim_offsets + anim * 2)
        if not r:
            return False
        mask = self._u16(r)
        r += 2
        limb = 0
        while mask & 0xFFFF:
            if mask & 0x8000:
                j = self._u16(r)
                r += 2
                if j == 0xFFFF:
                    state["pos"][limb] = 0xFFFF
                else:
                    extra = self.data[r]
                    r += 1
                    cmd = self.data[self.anim_cmds + j]
                    if cmd == 0x7A:
                        state["stopped"] &= ~(1 << limb)
                    elif cmd == 0x79:
                        state["stopped"] |= 1 << limb
                    else:
                        state["pos"][limb] = state["start"][limb] = j
                        state["end"][limb] = j + (extra & 0x7F)
                        state["noloop"][limb] = bool(extra & 0x80)
            limb += 1
            mask <<= 1
        return True

    def advance(self, state):
        """increaseAnim für alle Limbs (Sound-Befehle 0x78/0x7C überspringen)."""
        for limb in range(16):
            i = state["pos"][limb]
            if i == 0xFFFF:
                continue
            start, end = state["start"][limb], state["end"][limb]
            for _ in range(end - start + 2):
                if state["noloop"][limb]:
                    if i != end:
                        i += 1
                else:
                    i = start if i >= end else i + 1
                if self.data[self.anim_cmds + i] not in (0x78, 0x7C) or start == end:
                    break
            state["pos"][limb] = i

    def render(self, state, room_palette, size=(160, 160), origin=(80, 140)):
        """Setzt die Limbs zu einem RGBA-Bild zusammen (Blick nach rechts).
        origin ist der Fußpunkt des Actors im Bild."""
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        px = img.load()
        shr, mask = (4, 15) if self.colors == 16 else (3, 7)
        xmove = ymove = 0
        for limb in range(16):
            i = state["pos"][limb]
            if i == 0xFFFF or state["stopped"] & (1 << limb):
                continue
            code = self.data[self.anim_cmds + i] & 0x7F
            if code == 0x7B:
                continue
            frame_table = self._u16(self.frame_offsets + limb * 2)
            src = self._u16(frame_table + code * 2)
            width, height = self._u16(src), self._u16(src + 2)
            x0 = origin[0] + xmove + self._s16(src + 4)
            y0 = origin[1] + ymove + self._s16(src + 6)
            xmove += self._s16(src + 8)
            ymove -= self._s16(src + 10)
            at = src + 12
            x = y = 0
            while x < width:
                b = self.data[at]
                at += 1
                color, run = b >> shr, b & mask
                if not run:
                    run = self.data[at]
                    at += 1
                for _ in range(run):
                    if x >= width:
                        break
                    if color and 0 <= x0 + x < size[0] and 0 <= y0 + y < size[1]:
                        rgb = room_palette[self.palette[color]]
                        px[x0 + x, y0 + y] = (*rgb, 255)
                    y += 1
                    if y >= height:
                        y = 0
                        x += 1
        return img


# --------------------------------------------------------------------------
# Streifen-Codecs (gfx.cpp: decompressBitmap und Verwandte)
# --------------------------------------------------------------------------

class BitReader:
    """LSB-first Bitleser wie FILL_BITS/READ_BIT in ScummVM."""

    def __init__(self, data, pos):
        self.data = data
        self.pos = pos
        self.bits = 0
        self.count = 0

    def read(self, n):
        while self.count < n:
            self.bits |= self.data[self.pos] << self.count
            self.pos += 1
            self.count += 8
        v = self.bits & ((1 << n) - 1)
        self.bits >>= n
        self.count -= n
        return v


def decode_strip(bm, offset, pixels, x0, pitch, height):
    code = bm[offset]
    src = offset + 1
    shift = code % 10

    def put(x, y, c):
        pixels[y * pitch + x0 + x] = c & 0xFF

    if code == 1:  # roh
        for y in range(height):
            for x in range(8):
                put(x, y, bm[src])
                src += 1
    elif 14 <= code <= 18 or 34 <= code <= 38:   # Zickzack vertikal (+transparent)
        _basic(bm, src, shift, height, lambda i: (i // height, i % height), put)
    elif 24 <= code <= 28 or 44 <= code <= 48:   # Zickzack horizontal (+transparent)
        _basic(bm, src, shift, height, lambda i: (i % 8, i // 8), put)
    elif 64 <= code <= 68 or 84 <= code <= 88 or 104 <= code <= 108 or 124 <= code <= 128:
        _majmin(bm, src, shift, height, put)
    else:
        raise ScummError(f"unbekannter Streifen-Codec {code}")


def _basic(bm, src, shift, height, pos, put):
    """drawStripBasicV/H: 1 Bit „gleich“, 2 Bit „neue Farbe“, 3/4 Bit ±1."""
    color = bm[src]
    r = BitReader(bm, src + 1)
    inc = -1
    for i in range(8 * height):
        x, y = pos(i)
        put(x, y, color)
        if not r.read(1):
            continue
        if not r.read(1):
            color = r.read(shift)
            inc = -1
        elif not r.read(1):
            color = (color + inc) & 0xFF
        else:
            inc = -inc
            color = (color + inc) & 0xFF


def _majmin(bm, src, shift, height, put):
    """MajMinCodec::decodeLine, zeilenweise über 8 Pixel."""
    color = bm[src]
    r = BitReader(bm, src + 1)
    repeat = 0
    for i in range(8 * height):
        put(i % 8, i // 8, color)
        if repeat:
            repeat -= 1
            continue
        if r.read(1):
            if r.read(1):
                diff = r.read(3) - 4
                if diff:
                    color = (color + diff) & 0xFF
                else:
                    repeat = r.read(8) - 1
            else:
                color = r.read(shift)


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("game_dir", type=Path, help="Verzeichnis mit DISK01.LEC …")
    ap.add_argument("--out", type=Path, help="Räume als room_NNN.png hierhin schreiben")
    ap.add_argument("--room", type=int, action="append", help="nur diese Raumnummer(n)")
    args = ap.parse_args()
    try:
        rooms = read_rooms(args.game_dir)
    except ScummError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    for n in sorted(rooms):
        if args.room and n not in args.room:
            continue
        r = rooms[n]
        line = f"Raum {n:3d}: {r.width}x{r.height}, {len(r.boxes)} Boxen, {len(r.objects)} Objekte"
        if args.room:
            for i, box in enumerate(r.boxes):
                line += f"\n    Box {i}: {box.corners} flags={box.flags:#x}"
            for o in r.objects:
                line += (f"\n    Objekt {o.number:4d} {o.name!r:24} bei {o.x},{o.y} "
                         f"{o.width}x{o.height} Laufziel {o.walk_x},{o.walk_y} Richtung {o.direction}")
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            try:
                r.image().save(args.out / f"room_{n:03d}.png")
            except ScummError as e:
                line += f"  FEHLER: {e}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
