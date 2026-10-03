#!/usr/bin/env python3
"""advc – adventure compiler for Pocket Adventure FX.

Translates the scene description (game/*.adv) together with graphics and music into
  * a data block game.bin, which goes into the FX flash as the only resource,
  * gamedata.h with record structs, opcodes, IDs and a build ID.

Both files are produced in the same run from the same record definitions
(RECORDS below). This way engine and data cannot drift apart structurally;
the engine detects an outdated FX file by its build ID.

Byte order: little-endian like the AVR, so the engine can read records with
FX::readDataObject() directly into its structs. The only exception are
image headers (width/height big-endian), whose format FX::drawBitmap() dictates.

Language of the .adv file: see README.md, section "Script language".
"""
import argparse
import hashlib
import re
import shlex
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import original as orig
import scumm_text
from textsource import REF, TextError, TextSource, wrap

NONE8 = 0xFF
NONE24 = 0xFFFFFF

# Text box: 21 characters at 6 px = 126 px plus 1 px edge per side = 128 px.
TEXT_COLS = 21
TEXT_ROWS = 4
OPTION_COLS = 19   # plus selection mark on the left and arrow column on the right
# Size levels for characters with depth (actor … depth), as a fraction of full size
DEPTH_LEVELS = [1.0, 0.82, 0.66, 0.52, 0.4, 0.3, 0.21, 0.13]
MAX_OPTIONS = 16  # max. options visible at once (engine RAM: as many as needed)
CHOICE_ROWS = 4   # visible rows of the option list (fewer if the full text of the selected one needs more room)
# Display: 8 text rows. At the bottom the option list (one row per option), at the top
# the full text of the selected option, if it wraps.
SCREEN_ROWS = 8
MAX_INVENTORY = 16

DIRS = {"right": 0, "left": 1, "front": 2}

# The engine's own texts per language (no game texts, those come from the
# copy of the game), character set CP437. sound_on/sound_off/empty/grey_on/
# grey_off: at most 21 characters (one line; grey_* is the menu line for
# switching between greyscale and black and white). Usable in say as "ui.<key>", e.g. for
# exits to rooms that this version doesn't contain.
UI_TEXT = {
    "en": {"sound_on": "A:Start  B:Sound on", "sound_off": "A:Start  B:Sound off", "empty": "(empty)",
           "not_included": "Not included in this version.",
           "grey_on": "Greyscale: on", "grey_off": "Greyscale: off"},
    "de": {"sound_on": "A:Start  B:Ton an", "sound_off": "A:Start  B:Ton aus", "empty": "(leer)",
           "not_included": "In dieser Fassung nicht enthalten.",
           "grey_on": "Graustufen: an", "grey_off": "Graustufen: aus"},
    "fr": {"sound_on": "A:Jouer  B:Son oui", "sound_off": "A:Jouer  B:Son non", "empty": "(vide)",
           "not_included": "Pas inclus dans cette version.",
           "grey_on": "Niveaux de gris : oui", "grey_off": "Niveaux de gris : non"},
    "it": {"sound_on": "A:Gioca  B:Audio sì", "sound_off": "A:Gioca  B:Audio no", "empty": "(vuoto)",
           "not_included": "Non incluso in questa versione.",
           "grey_on": "Scala di grigi: sì", "grey_off": "Scala di grigi: no"},
    "es": {"sound_on": "A:Jugar  B:Sonido sí", "sound_off": "A:Jugar  B:Sonido no", "empty": "(vacío)",
           "not_included": "No incluido en esta versión.",
           "grey_on": "Escala de grises: sí", "grey_off": "Escala de grises: no"},
}
UI_REF = re.compile(r"ui\.(\w+)")

# Timer3 runs at F_CPU/8 = 2 MHz in CTC mode and toggles the pin on every
# compare match: f = 2 MHz / (2 * (OCR + 1)).
TIMER3_HALF_CLOCK = 1_000_000

# --------------------------------------------------------------------------
# Record definitions: single source for packing (Python) and structs (C++)
# --------------------------------------------------------------------------

RECORDS = {
    # At the start of game.bin: one LangEntry per included language with its
    # name (for the language selection) and its GameHeader. Images, music and
    # walk paths are shared between the languages; texts, verbs, objects and scripts
    # each language has on its own.
    "LangDir": [("magic", "u16"), ("buildId", "u16"), ("count", "u8")],
    "LangEntry": [("name", "u24"), ("header", "u24")],
    "GameHeader": [
        ("verbCount", "u8"), ("verbs", "u24"),
        ("objectCount", "u8"), ("objects", "u24"),
        ("actorCount", "u8"), ("actors", "u24"),
        ("roomCount", "u8"), ("rooms", "u24"),
        ("musicCount", "u8"), ("music", "u24"),
        ("startScript", "u24"), ("cursor", "u24"),
        # titleGrey: the same image in greyscale (3 planes as frames), NONE24 = none
        ("title", "u24"), ("titleGrey", "u24"), ("titleMusic", "u8"),
        # Verb for inventory clicks with "walk" (e.g. look), otherwise NONE8
        ("inventoryVerb", "u8"),
        # The engine's own texts (not from the game): title line, empty
        # inventory, menu line greyscale on/off
        ("uiSoundOn", "u24"), ("uiSoundOff", "u24"), ("uiEmpty", "u24"),
        ("uiGreyOn", "u24"), ("uiGreyOff", "u24"),
    ],
    # prep: connecting word for two-object sentences ("with", "on"), otherwise NONE24
    "VerbRec": [("name", "u24"), ("prep", "u24"), ("fallback", "u24")],
    # verbs points to a list of VerbEntry, terminated with verb == NONE8
    "ObjectRec": [("name", "u24"), ("verbs", "u24")],
    "VerbEntry": [("verb", "u8"), ("other", "u8"), ("script", "u24")],
    # object: object for which the character itself is the hotspot (NONE8 = none);
    # the hotspot then moves along with the character.
    # grey: the same frames in greyscale (3 planes per frame), NONE24 = none
    "ActorRec": [
        ("sprite", "u24"), ("grey", "u24"),
        ("stand", "u8"), ("walkFirst", "u8"), ("walkCount", "u8"),
        ("talk", "u8"), ("front", "u8"), ("frontTalk", "u8"),
        ("object", "u8"),
        # depth: size levels for rooms with depth (NONE24 = always the same size);
        # table: u8 count, per level u8 minimum size (0–255) + u24 sprite
        # + u24 sprite in greyscale (NONE24 = none)
        ("depth", "u24"),
    ],
    # matrix: boxCount × boxCount bytes, entry [from][to] = next box on
    # the shortest path (NONE8 = unreachable), precomputed by the compiler.
    # grey: background in greyscale, one frame per plane (NONE24 = 1 bit only)
    "RoomRec": [
        ("background", "u24"), ("grey", "u24"), ("width", "u16"), ("height", "u8"),
        ("boxCount", "u8"), ("boxes", "u24"), ("matrix", "u24"),
        ("placeCount", "u8"), ("places", "u24"),
        ("entry", "u24"),
    ],
    # Walkable quadrilateral as in SCUMM: corners top left, top right, bottom
    # right, bottom left. May degenerate into a line or a point.
    "BoxRec": [
        ("ulx", "u16"), ("uly", "u8"), ("urx", "u16"), ("ury", "u8"),
        ("lrx", "u16"), ("lry", "u8"), ("llx", "u16"), ("lly", "u8"),
        # Character size (255 = full) at the top and bottom edge; in between
        # linear in y – like the original's scaling levels (SA block)
        ("scaleTop", "u8"), ("scaleBottom", "u8"),
    ],
    # An object at a position in the room. image == NONE24: hotspot only.
    # grey: image in greyscale (cutout of the background: doors, items),
    # frame f of the 1-bit image is stored there as frames 3f..3f+2 (one per plane);
    # NONE24: the 1-bit image applies to all planes (characters as decor).
    # backdrop: 1 if state 0 shows exactly the background (a closed
    # door) – then the engine draws nothing.
    "PlaceRec": [
        ("object", "u8"), ("x", "u16"), ("y", "u8"), ("w", "u8"), ("h", "u8"),
        ("walkX", "u16"), ("walkY", "u8"), ("face", "u8"),
        ("image", "u24"), ("grey", "u24"), ("frames", "u8"), ("speed", "u8"), ("backdrop", "u8"),
    ],
    "TrackRec": [("count", "u16"), ("loop", "u8")],
    "NoteRec": [("ocr", "u16"), ("ms", "u8")],
}

SIZES = {"u8": 1, "u16": 2, "u24": 3}
CTYPES = {"u8": "uint8_t", "u16": "uint16_t", "u24": "__uint24"}


def record_size(name):
    return sum(SIZES[t] for _, t in RECORDS[name])


# Opcodes: (name, operands). Operand types as above, 'addr' = u24 jump target.
OPCODES = [
    ("END", []),
    ("SAY", ["u8", "u24"]),          # actor|NONE8=narrator, string
    ("WALK", ["u8", "u16", "u8"]),   # actor, x, y – blocks until arrival
    ("PUT", ["u8", "u16", "u8"]),    # actor, x, y – puts him into the current room
    ("FACE", ["u8", "u8"]),          # actor, dir
    ("SET", ["u8"]),                 # flag
    ("CLEAR", ["u8"]),               # flag
    ("JUNLESS", ["u8", "u8", "u24"]),  # cond-kind, index, target: jumps if condition is FALSE
    ("JMP", ["u24"]),
    ("PICKUP", ["u8"]),              # object → inventory
    ("LOSE", ["u8"]),                # remove object from the inventory (used up)
    ("STATE", ["u8", "u8"]),         # object, state (image frame group)
    ("HIDE", ["u8"]),
    ("SHOW", ["u8"]),
    ("MUSIC", ["u8"]),               # track|NONE8=stop
    ("WAIT", ["u8"]),                # frames
    ("CHOICES", []),                 # begin a new dialogue choice
    ("OPTION", ["u24", "u24"]),      # text, target: offer option (preceded by condition code if needed)
    ("ASK", []),                     # show choice and wait; continue if there are no options
    ("ROOM", ["u8", "u16", "u8", "u8"]),  # room, x|0xFFFF, y, dir: load, place player character, call entry
    ("CARD", ["u24", "u24", "u8"]),  # full screen, same in greyscale|NONE24, music|NONE8: until the music ends (without: 3 s) or A
    ("WAITR", ["u16", "u16"]),       # wait a random min … max frames
    ("JUNLESSPOS", ["u8", "u8", "u16", "u24"]),  # POS_Y|POS_GT|COND_NOT, actor, value, target: jumps if the comparison fails
    ("START", ["u24"]),              # start background routine (replaces a running one)
    ("STOP", ["u24"]),               # end this background routine
    ("RANDOM", ["u8"]),              # n, then n × u24 targets: jumps to one of them (uniformly distributed)
    ("SETSTR", ["u8", "u24"]),       # slot, text: set string variable
    ("SETCHAR", ["u8", "u8", "u8"]), # slot, position, char ('@' is not shown)
    ("LET", ["u8", "u8"]),           # variable, value
    ("FLASH", ["u8"]),               # frames: image flashes inverted (waits)
    ("PAN", ["u16"]),                # pan camera to x (waits), until the player character is placed again
    ("COSTUME", ["u8", "u8"]),       # actor, as-actor: show with the latter's graphics
    ("PLAY", ["u24"]),               # start cutscene as foreground script (from a background routine)
    ("CALL", ["u24"]),               # call subroutine (sub)
    ("LETR", ["u8", "u8", "u8"]),    # variable, min, max: random value
    ("SETCHARV", ["u8", "u8", "u8"]),  # slot, position, variable: character from a variable
    ("ADD", ["u8", "u8"]),           # variable, addend (two's complement, result mod 256)
    ("JUNLESSV", ["u8", "u8", "u8", "u24"]),  # VAR_*|COND_NOT, variable, value, target
    ("REMOVE", ["u8"]),              # remove actor from the room (e.g. behind a door)
    ("HALT", ["u8"]),                # actor stops where he currently is
]
OP = {name: i for i, (name, _) in enumerate(OPCODES)}

# Condition kinds for JUNLESS and dialogue options
COND_FLAG = 0x00
COND_HAS = 0x01
COND_OPEN = 0x02
COND_HOVER = 0x03     # mouse pointer is over the object
POS_Y, POS_GT = 0x01, 0x02   # JUNLESSPOS: y instead of x, > instead of <
VERB_ANY = 0xFE      # VerbEntry.verb for "on other" (all remaining verbs except walk to)
VAR_CMP = {"=": 0x00, "<": 0x01, ">": 0x02}   # comparison in JUNLESSV
WALK_DIRECT = 0xFFFF  # PlaceRec.walkX: script starts immediately (place … direct)
COND_NOT = 0x80



class CompileError(Exception):
    pass


# --------------------------------------------------------------------------
# Binary output with labels and fixups
# --------------------------------------------------------------------------

class Blob:
    def __init__(self):
        self.data = bytearray()
        self.labels = {}
        self.fixups = []  # (offset, label)

    def here(self):
        return len(self.data)

    def mark(self, label):
        if label in self.labels:
            raise CompileError(f"internal error: duplicate label: {label}")
        self.labels[label] = self.here()

    def put(self, typ, value):
        n = SIZES[typ]
        if isinstance(value, str):
            self.fixups.append((self.here(), value))
            value = 0
        if not 0 <= value < (1 << (8 * n)):
            raise CompileError(f"value {value} does not fit into {typ}")
        self.data += value.to_bytes(n, "little")

    def record(self, record_name, /, **fields):
        spec = RECORDS[record_name]
        if set(fields) != {f for f, _ in spec}:
            raise CompileError(f"internal error: fields for {record_name}: {sorted(fields)}")
        for f, t in spec:
            self.put(t, fields[f])

    def raw(self, b):
        self.data += b

    def resolve(self):
        for off, label in self.fixups:
            if label not in self.labels:
                raise CompileError(f"internal error: missing label: {label}")
            self.data[off:off + 3] = self.labels[label].to_bytes(3, "little")


# --------------------------------------------------------------------------
# Images in FX format (identical to fxdata-build.py)
# --------------------------------------------------------------------------

def encode_image(path, mask=False):
    """Encodes a PNG; frame size from the file name: name_WxH.png."""
    m = re.search(r"_(\d+)x(\d+)$", path.stem)
    img = Image.open(path).convert("RGBA")
    frame = (int(m.group(1)), int(m.group(2))) if m else None
    return encode_pil(img, path.name, frame, mask)


def encode_pil(img, name, frame=None, mask=False):
    """Encodes an image like fxdata-build.py: header (w, h big-endian), then
    per frame column-wise bytes per 8-pixel row; with transparency each byte is
    followed by its mask byte. mask=True forces the mask even for opaque
    images – for everything the engine draws with dbmMasked."""
    img = img.convert("RGBA")
    fw, fh = frame or img.size
    if img.width % fw or img.height % fh:
        raise CompileError(f"{name}: image size is not a multiple of {fw}x{fh}")
    px = img.load()
    masked = mask or img.getchannel("A").getextrema()[0] < 255
    out = bytearray([fw >> 8, fw & 0xFF, fh >> 8, fh & 0xFF])
    frames = 0
    for fy in range(0, img.height, fh):
        for fx in range(0, img.width, fw):
            for y in range(0, fh, 8):
                for x in range(fw):
                    b = m_ = 0
                    for p in range(8):
                        if y + p < fh:
                            r, g, bl, a = px[fx + x, fy + y + p]
                            if a > 64:
                                m_ |= 1 << p
                                if g > 64:
                                    b |= 1 << p
                    out.append(b)
                    if masked:
                        out.append(m_)
            frames += 1
    return bytes(out), fw, fh, frames


# --------------------------------------------------------------------------
# Music
# --------------------------------------------------------------------------

def parse_inline_notes(spec):
    notes = []
    for tok in spec.split():
        try:
            hz, ms = tok.split(":")
            notes.append((int(hz), int(ms)))
        except ValueError:
            raise CompileError(f"note '{tok}' not in the format Hz:ms")
    return notes


def encode_track(notes):
    """Frequency/duration → (Timer3 OCR, ms ≤ 255). Long notes are split;
    the player doesn't reset the counter for the same OCR, so no audible
    seam occurs."""
    out = []
    for hz, ms in notes:
        if hz < 0 or ms <= 0:
            raise CompileError(f"invalid note {hz}:{ms}")
        ocr = 0 if hz == 0 else round(TIMER3_HALF_CLOCK / hz) - 1
        if not 0 <= ocr <= 0xFFFF:
            raise CompileError(f"frequency {hz} Hz outside the timer range")
        while ms > 0:
            chunk = min(ms, 255)
            out.append((ocr, chunk))
            ms -= chunk
    return out


# --------------------------------------------------------------------------
# Walk boxes: adjacency and pathfinding matrix
# --------------------------------------------------------------------------

def _seg_point_dist(a, b, p):
    (ax, ay), (bx, by), (px, py) = a, b, p
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_intersect(a, b, c, d):
    d1, d2, d3, d4 = _cross(c, d, a), _cross(c, d, b), _cross(a, b, c), _cross(a, b, d)
    return ((d1 > 0) != (d2 > 0) and d1 != 0 and d2 != 0 and
            (d3 > 0) != (d4 > 0) and d3 != 0 and d4 != 0)


def _inside(quad, p):
    """Point in the (convex) quadrilateral; degenerate quadrilaterals have no interior."""
    signs = [_cross(quad[i], quad[(i + 1) % 4], p) for i in range(4) if quad[i] != quad[(i + 1) % 4]]
    return len(signs) >= 3 and (all(s >= 0 for s in signs) or all(s <= 0 for s in signs))


def box_distance(q1, q2):
    """Smallest distance between two walk boxes (0 when touching/overlapping)."""
    edges1 = [(q1[i], q1[(i + 1) % 4]) for i in range(4)]
    edges2 = [(q2[i], q2[(i + 1) % 4]) for i in range(4)]
    if any(_segments_intersect(a, b, c, d) for a, b in edges1 for c, d in edges2):
        return 0.0
    if any(_inside(q2, p) for p in q1) or any(_inside(q1, p) for p in q2):
        return 0.0
    return min(min(_seg_point_dist(c, d, p) for c, d in edges2 for p in q1),
               min(_seg_point_dist(a, b, p) for a, b in edges1 for p in q2))


# Walk boxes that come within this distance of each other count as connected.
# Corners that coincide in the original have distance 0; the tolerance
# only catches rounding in hand-written rooms.
BOX_TOUCH = 0.6


def box_matrix(quads):
    """Next-box matrix via breadth-first search: m[from][to] = first step."""
    n = len(quads)
    adjacent = [[i != j and box_distance(quads[i], quads[j]) <= BOX_TOUCH for j in range(n)]
                for i in range(n)]
    matrix = []
    for start in range(n):
        first = [NONE8] * n
        first[start] = start
        queue = []
        for j in range(n):
            if adjacent[start][j]:
                first[j] = j
                queue.append(j)
        while queue:
            cur = queue.pop(0)
            for j in range(n):
                if adjacent[cur][j] and first[j] == NONE8:
                    first[j] = first[cur]
                    queue.append(j)
        matrix.append(first)
    return matrix


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

class Line:
    def __init__(self, no, tokens):
        self.no = no
        self.tokens = tokens

    def err(self, msg):
        return CompileError(f"line {self.no}: {msg}")


def strip_comment(raw):
    """Cuts off a comment. '#' only starts one at the beginning of a word and
    outside of quotes – in text references like r38.s203#1 it is
    part of the word."""
    quote = None
    for i, c in enumerate(raw):
        if quote:
            if c == "\\" and quote == '"':
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#" and (i == 0 or raw[i - 1].isspace()):
            return raw[:i]
    return raw


def tokenize(text):
    lines = []
    for no, raw in enumerate(text.splitlines(), 1):
        try:
            toks = shlex.split(strip_comment(raw))
        except ValueError as e:
            raise CompileError(f"line {no}: {e}")
        if toks:
            lines.append(Line(no, toks))
    return lines


def with_lead_in(melody, lead):
    """Starts the melody only after a pause (like the chapter card), plays
    the notes of an accompanying voice until then – the speaker is monophonic,
    in the original the voices run side by side. Length and tempo stay the same."""
    rest = 0
    while rest < len(melody) and melody[rest][0] == 0:
        rest += 1
    gap = sum(ms for _, ms in melody[:rest])
    out, t = [], 0
    for hz, ms in lead:
        if t >= gap:
            break
        ms = min(ms, gap - t)
        out.append((hz, ms))
        t += ms
    if t < gap:
        out.append((0, gap - t))
    return out + melody[rest:]


def box_scale(box, slots):
    """Character size (1–255) at the top and bottom edge of an original box:
    fixed, or with 0x8000 | n linear in y according to level n (ScummVM getScale)."""
    ys = [y for _, y in box.corners]
    if not box.scale & 0x8000:
        v = max(1, min(255, box.scale))
        return v, v
    s1, y1, s2, y2 = slots[box.scale & 0x7FFF]
    if y1 == y2:
        raise CompileError(f"invalid scale level {box.scale & 0x7FFF}")

    def at(y):
        return max(1, min(255, round(s1 + (s2 - s1) * (y - y1) / (y2 - y1))))
    return at(min(ys)), at(max(ys))


class Game:
    def __init__(self, base):
        self.base = base
        self.verbs = {}      # id → dict
        self.objects = {}
        self.actors = {}
        self.rooms = {}
        self.music = {}
        self.flags = {}
        self.vars = {}       # numeric variables (0…255): name → number
        self.defaults = {}   # verb id → script AST
        self.start_room = None
        self.cursor = None
        self.title = None
        self.title_source = None  # cutout of the original for the greyscale
        self.title_music = None
        self.cards = {}      # id → {"room", "threshold", "music", "line"}
        # greyscale per image: ("room", id) | ("card", id) | ("title", None)
        # → {"layers", "subjects", "line"} (original.to_grey)
        self.greyscale = {}
        self.routines = {}   # id → (line, AST): background routines
        self.cutscenes = {}  # id → (line, AST): scenes a background routine starts with play
        self.subs = {}       # id → (line, AST): subroutines (call)
        self.numbers = {}    # variable → number in the original (code 4 in texts)
        self.strings = {}    # id → {"original": number, "slot", "line"}: string variables
        self.string_refs = {}  # id → text references from setstring (for the length)
        self.inventory_verb = None
        self.original_dir = None   # graphics, walk paths, music: from the first copy
        self.languages = []        # TextSource per given copy (language version)
        self._original_rooms = None

    def original_room(self, number, ln):
        """Room from the original data (loaded once, then cached)."""
        if self.original_dir is None:
            raise ln.err("needs the original data: advc.py --original <directory> "
                         "(in the Makefile: make ORIGINAL=<directory>)")
        if self._original_rooms is None:
            import scumm_v4
            try:
                self._original_rooms = scumm_v4.read_rooms(self.original_dir)
            except scumm_v4.ScummError as e:
                raise ln.err(f"original data: {e}")
        if number not in self._original_rooms:
            raise ln.err(f"room {number} does not exist in the original data")
        return self._original_rooms[number]

    def original_room_of(self, game_dir, number, ln):
        """Room from a specific copy (images that differ per language,
        e.g. chapter cards with drawn-in text)."""
        import scumm_v4
        cache = self.__dict__.setdefault("_rooms_by_dir", {})
        if game_dir not in cache:
            try:
                cache[game_dir] = scumm_v4.read_rooms(game_dir)
            except scumm_v4.ScummError as e:
                raise ln.err(f"original data: {e}")
        if number not in cache[game_dir]:
            raise ln.err(f"room {number} does not exist in {game_dir}")
        return cache[game_dir][number]

    def original_costume(self, number, ln):
        self.original_room(1, ln)  # checks the directory and loads the rooms
        import scumm_v4
        try:
            return scumm_v4.read_costume(self.original_dir, number)
        except scumm_v4.ScummError as e:
            raise ln.err(f"original data: {e}")


class Parser:
    def __init__(self, game, lines):
        self.g = game
        self.lines = lines
        self.i = 0

    def next(self):
        if self.i >= len(self.lines):
            return None
        line = self.lines[self.i]
        self.i += 1
        return line

    # ---- Top level ------------------------------------------------------

    def parse(self):
        while (ln := self.next()) is not None:
            kw, *args = ln.tokens
            handler = getattr(self, f"top_{kw}", None)
            if handler is None:
                raise ln.err(f"unknown statement '{kw}'")
            handler(ln, args)

    def new_id(self, table, ln, ident, what):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", ident):
            raise ln.err(f"invalid identifier '{ident}'")
        if ident in table:
            raise ln.err(f"{what} '{ident}' defined twice")
        return ident

    def top_verb(self, ln, args):
        if len(args) not in (2, 4) or (len(args) == 4 and args[2] != "prep"):
            raise ln.err("Syntax: verb <id> <text> [prep <text>]  (texts as references, e.g. s22#10)")
        if args[0] == "other":
            raise ln.err("'other' is reserved (on other = all remaining verbs)")
        vid = self.new_id(self.g.verbs, ln, args[0], "verb")
        self.g.verbs[vid] = {"name": self.text_ref(ln, args[1]),
                             "prep": self.text_ref(ln, args[3]) if len(args) == 4 else None,
                             "index": len(self.g.verbs)}

    def top_default(self, ln, args):
        if len(args) != 1:
            raise ln.err("Syntax: default <verb>")
        verb = self.ref(self.g.verbs, ln, args[0], "verb")
        if verb in self.g.defaults:
            raise ln.err(f"default {verb} given twice")
        self.g.defaults[verb] = self.block(("end",))[0]

    def top_music(self, ln, args):
        if len(args) >= 3 and args[1] == "original":
            self.original_music(ln, args)
            return
        if len(args) < 3 or args[1] != "notes":
            raise ln.err('Syntax: music <id> notes "<Hz:ms …>" [loop] '
                         '| music <id> original <sound> [channel N] [start MS]')
        mid = self.new_id(self.g.music, ln, args[0], "music")
        loop = args[3:] == ["loop"]
        if args[3:] not in ([], ["loop"]):
            raise ln.err(f"unexpected: {args[3:]}")
        notes = parse_inline_notes(args[2])
        self.g.music[mid] = {"notes": encode_track(notes), "loop": loop, "index": len(self.g.music)}

    def original_music(self, ln, args):
        """Music from an original sound resource: the melody voice of the
        AdLib version (the PC speaker version is only a placeholder for the
        room music). Loops as in the original."""
        import scumm_v4
        mid = self.new_id(self.g.music, ln, args[0], "music")
        number = self.num(ln, args[2])
        opts = self.keyvals(ln, args[3:], {"channel": 1, "start": 1, "lead": 1})
        self.g.original_room(1, ln)  # checks the original directory
        try:
            sound = scumm_v4.read_sound(self.g.original_dir, number)
            if "AD" not in sound:
                raise scumm_v4.ScummError(f"sound {number} has no AdLib version")
            notes, loop, _ = scumm_v4.adlib_melody(sound["AD"], opts.get("channel", [None])[0])
            if "lead" in opts:
                lead, _, _ = scumm_v4.adlib_melody(sound["AD"], opts["lead"][0])
                notes = with_lead_in(notes, lead)
        except scumm_v4.ScummError as e:
            raise ln.err(f"original data: {e}")
        # start <ms>: skip the intro (e.g. the long sound carpet before
        # the title theme, which a monophonic speaker can't reproduce).
        skip = opts.get("start", [0])[0]
        while notes and skip > 0:
            hz, ms = notes[0]
            if ms <= skip:
                notes.pop(0)
                skip -= ms
            else:
                notes[0] = (hz, ms - skip)
                skip = 0
        if not notes:
            raise ln.err(f"start {opts['start'][0]}: no note left after it")
        self.g.music[mid] = {"notes": encode_track(notes), "loop": loop, "index": len(self.g.music)}

    def top_actor(self, ln, args):
        # actor <id> sprite "<png>" stand N walk A B talk N front N fronttalk N
        # actor <id> costume <nr> [scale S] [palette ROOM] [dark D] [outline O]
        # actor <id> invisible
        args = args[:1] + [None] + args[1:]   # slot of the former name: indices stay the same
        if len(args) >= 4 and args[2] == "costume":
            self.costume_actor(ln, args)
            return
        if len(args) == 3 and args[2] == "invisible":
            # speaker without a character of their own, e.g. people who are part of the background
            aid = self.new_id(self.g.actors, ln, args[0], "actor")
            self.g.actors[aid] = {
                "sprite": ("pil", "invisible", Image.new("RGBA", (1, 1), (0, 0, 0, 0))),
                "stand": 0, "walk": [0, 1], "talk": 0, "front": 0, "fronttalk": 0,
                "index": len(self.g.actors), "line": ln,
            }
            return
        if len(args) < 4 or args[2] != "sprite":
            raise ln.err('Syntax: actor <id> sprite "<png>" stand N walk FIRST COUNT talk N front N fronttalk N '
                         '| actor <id> costume <nr> [scale S] [palette ROOM] [dark D] [outline O] | actor <id> invisible')
        aid = self.new_id(self.g.actors, ln, args[0], "actor")
        opts = self.keyvals(ln, args[4:], {"stand": 1, "walk": 2, "talk": 1, "front": 1, "fronttalk": 1})
        stand = opts.get("stand", [0])[0]
        talk = opts.get("talk", [stand])[0]
        front = opts.get("front", [stand])[0]
        walk = opts.get("walk", [stand, 1])
        self.g.actors[aid] = {
            "sprite": args[3], "stand": stand, "walk": walk, "talk": talk,
            "front": front, "fronttalk": opts.get("fronttalk", [front])[0],
            "index": len(self.g.actors), "line": ln,
        }

    def costume_options(self, ln, toks):
        # Default outline 2 px: with 1 px, characters get lost in bright, detailed
        # rooms like the kitchen in the dither pattern of the background.
        opts = self.keyvals(ln, toks, {"scale": 1, "palette": 1, "dark": 1, "outline": 1, "object": 1, "depth": 0})
        return (opts.get("scale", [0.55])[0], opts.get("palette", [38])[0],
                opts.get("dark", [45])[0], opts.get("outline", [2])[0],
                opts.get("object", [None])[0], "depth" in opts)

    def costume_actor(self, ln, args):
        """Actor from an original costume: standing, walk cycle, talking, front,
        front talking – all facing right; the engine mirrors for left."""
        import scumm_v4 as sv
        aid = self.new_id(self.g.actors, ln, args[0], "actor")
        number = self.num(ln, args[3])
        scale, palette_room, dark, outline, obj, depth = self.costume_options(ln, args[4:])
        costume = self.g.original_costume(number, ln)
        palette = self.g.original_room(palette_room, ln).palette
        stand = [sv.INIT, sv.STAND]
        walk = stand + [sv.WALK]
        talk = stand + [sv.TALK_START]
        steps = orig.cycle_length(costume, walk, sv.RIGHT)
        seqs = [(stand, sv.RIGHT, 0)]
        seqs += [(walk, sv.RIGHT, k) for k in range(steps)]
        seqs += [(talk, sv.RIGHT, 1), (stand, sv.FRONT, 0), (talk, sv.FRONT, 1)]
        try:
            strip, fw, fh, grey = orig.costume_frames(costume, palette, seqs, scale, dark, outline, grey=True)
        except ValueError as e:
            raise ln.err(str(e))
        n = len(seqs)
        levels = []
        if depth:
            # Depth: the same frames in smaller levels, the engine picks one depending
            # on the size of the walk box (scaling at runtime would be too expensive)
            for f in DEPTH_LEVELS[1:]:
                try:
                    st, w, h, gr = orig.costume_frames(costume, palette, seqs, scale * f, dark, outline, grey=True)
                except ValueError as e:
                    raise ln.err(str(e))
                key = f"costume{number}_{round(scale * f, 3)}_{dark}_{outline}"
                levels.append((f, ("pil", key, st, (w, h)), ("grey", key + "_grey", gr, w)))
        key = f"costume{number}_{scale}_{dark}_{outline}"
        self.g.actors[aid] = {
            "sprite": ("pil", key, strip, (fw, fh)), "grey": ("grey", key + "_grey", grey, fw),
            "stand": 0, "walk": [1, steps], "talk": n - 3, "front": n - 2, "fronttalk": n - 1,
            "object": obj, "index": len(self.g.actors), "line": ln, "levels": levels,
        }

    def costume_place_image(self, ln, number, scales, palette_room, dark, outline):
        """Animated room object from a costume (standing animation), one
        frame group per state; the states differ in size.
        All frames get the common size, foot point at the bottom centre."""
        import scumm_v4 as sv
        costume = self.g.original_costume(number, ln)
        palette = self.g.original_room(palette_room, ln).palette
        stand = [sv.INIT, sv.STAND]
        steps = orig.cycle_length(costume, stand, sv.RIGHT)
        seqs = [(stand, sv.RIGHT, k) for k in range(steps)]
        strips = [orig.costume_frames(costume, palette, seqs, sc, dark, outline, grey=True) for sc in scales]
        fw = max(w for _, w, _, _ in strips)
        fh = max(h for _, _, h, _ in strips)
        out = Image.new("RGBA", (fw * steps * len(strips), fh), (0, 0, 0, 0))
        grey = Image.new("RGBA", out.size, (0, 0, 0, 0))
        for si, (strip, w, h, grey_strip) in enumerate(strips):
            for k in range(steps):
                at = ((si * steps + k) * fw + (fw - w) // 2, fh - h)
                out.paste(strip.crop((k * w, 0, (k + 1) * w, h)), at)
                grey.paste(grey_strip.crop((k * w, 0, (k + 1) * w, h)), at)
        key = f"costume{number}_" + "_".join(map(str, scales)) + f"_{dark}_{outline}"
        return ("pil", key, out, (fw, fh)), ("grey", key + "_grey", grey, fw), steps, fw, fh

    def top_object(self, ln, args):
        if len(args) != 2:
            raise ln.err("Syntax: object <id> <name>  (name as reference, e.g. o498.name)")
        oid = self.new_id(self.g.objects, ln, args[0], "object")
        obj = {"name": self.text_ref(ln, args[1]), "verbs": [], "index": len(self.g.objects), "line": ln}
        self.g.objects[oid] = obj
        # Verb handlers are only resolved after all objects have been read,
        # because 'on use <other_object>' may refer forward.
        while True:
            sub = self.next()
            if sub is None:
                raise ln.err(f"object {oid}: 'end' missing")
            kw, *a = sub.tokens
            if kw == "end" and not a:
                break
            if kw != "on" or len(a) not in (1, 2):
                raise sub.err("expected in object: on <verb> [<object>] … end")
            body = self.block(("end",))[0]
            obj["verbs"].append({"verb": a[0], "other": a[1] if len(a) == 2 else None,
                                 "body": body, "line": sub})

    def top_room(self, ln, args):
        syntax = ('Syntax: room <id> bg "<png>" | room <id> original <nr> [tone S W] [contrast K] [channel C] '
                  '| room <id> blank')
        if len(args) < 2 or args[1] not in ("bg", "original", "blank") or (args[1] != "blank" and len(args) < 3):
            raise ln.err(syntax)
        rid = self.new_id(self.g.rooms, ln, args[0], "room")
        room = {"boxes": [], "places": [], "entry": [], "index": len(self.g.rooms), "line": ln,
                "original": None, "scale": 1.0, "dx": 0}
        if args[1] == "blank":
            # Black image without walk boxes, e.g. for "Meanwhile …" (s117)
            if len(args) != 2:
                raise ln.err(syntax)
            room["bg"] = ("pil", f"blank_{rid}", Image.new("RGBA", (128, 64), (0, 0, 0, 255)))
            room["blank"] = True
        elif args[1] == "bg":
            if len(args) != 3:
                raise ln.err(syntax)
            room["bg"] = args[2]
        else:
            source = self.g.original_room(self.num(ln, args[2]), ln)
            opts = self.keyvals(ln, args[3:], {"tone": 2, "contrast": 1, "channel": 1, "overlay": None,
                                               "height": 1}, words=("channel",))
            black, white = opts.get("tone", [30, 95])
            channel = opts.get("channel", ["luminance"])[0]
            if channel not in orig.CHANNELS:
                raise ln.err(f"channel: {'/'.join(orig.CHANNELS)}")
            contrast = opts.get("contrast", [1.0])[0]
            # height: taller than the display (the map), the camera then also scrolls
            # vertically; otherwise at 64 px height
            height = opts.get("height", [64])[0]
            if not 64 <= height <= 255:
                raise ln.err("height: 64…255")
            room["scale"] = height / source.height
            size = (orig.scaled(source.width, room["scale"]), height)
            # Narrower than the display (the map of Mêlée): centred on black,
            # walk boxes and objects shifted by the same offset.
            room["dx"] = max(0, (128 - size[0]) // 2)
            # Object images that are a fixed part of the background (e.g. the pirates
            # in the SCUMM Bar), overlay them before the conversion.
            composite = source.image()
            for number in opts.get("overlay", []):
                composite.paste(self.object_image(ln, source, number), self.object_pos(ln, source, number))
            room["original"] = source
            room["composite"] = composite
            room["tone"] = (size, black, white, contrast, channel)
            mono = orig.to_mono(composite, size, black, white, contrast, channel)
            if room["dx"]:
                padded = Image.new("RGBA", (128, height), (0, 0, 0, 255))
                padded.paste(mono, (room["dx"], 0))
                mono = padded
            room["bg"] = ("pil", f"room{source.number}", mono)
        self.g.rooms[rid] = room
        while True:
            sub = self.next()
            if sub is None:
                raise ln.err(f"room {rid}: 'end' missing")
            kw, *a = sub.tokens
            if kw == "end" and not a:
                break
            if kw == "walkbox":
                if len(a) != 4:
                    raise sub.err("Syntax: walkbox x0 y0 x1 y1")
                x0, y0, x1, y1 = (self.num(sub, v) for v in a)
                room["boxes"].append(([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], sub, (255, 255)))
            elif kw == "walkboxes" and a == ["original"]:
                if not room["original"]:
                    raise sub.err("walkboxes original only in a room from the original data")
                s = room["scale"]
                for box in room["original"].boxes:
                    room["boxes"].append(([(x * s + room["dx"], y * s) for x, y in box.corners], sub,
                                          box_scale(box, room["original"].scale_slots)))
            elif kw == "place":
                room["places"].append(self.place(sub, a, room))
            elif kw == "entry" and not a:
                room["entry"] = self.block(("end",))[0]
            else:
                raise sub.err(f"unknown in room: '{kw}'")

    def top_start(self, ln, args):
        """start <room> or a start … end block (start script that begins
        with room <room> at x y)."""
        if len(args) == 1:
            self.g.start_room = (args[0], ln)
        elif not args:
            self.g.start_room = self.block(("end",))[0]
        else:
            raise ln.err("Syntax: start <room> | start … end")

    def top_inventoryverb(self, ln, args):
        if len(args) != 1:
            raise ln.err("Syntax: inventoryverb <verb>")
        self.g.inventory_verb = (args[0], ln)

    def top_cursor(self, ln, args):
        if len(args) != 1:
            raise ln.err('Syntax: cursor "<png>"')
        self.g.cursor = args[0]

    def top_routine(self, ln, args):
        """routine <id> … end: background routine that runs alongside the game (e.g. the cook
        who comes out of the kitchen at intervals). It doesn't lock input and
        pauses while a script is running (cutscene, dialogue)."""
        if len(args) != 1:
            raise ln.err("Syntax: routine <id>")
        rid = self.new_id(self.g.routines, ln, args[0], "routine")
        body, _ = self.block(("end",))
        self.g.routines[rid] = (ln, body)

    def top_sub(self, ln, args):
        """sub <id> … end: subroutine, called with call <id> – for
        parts of conversations that the original jumps to from several places."""
        if len(args) != 1:
            raise ln.err("Syntax: sub <id>")
        sid = self.new_id(self.g.subs, ln, args[0], "subroutine")
        body, _ = self.block(("end",))
        self.g.subs[sid] = (ln, body)

    def top_cutscene(self, ln, args):
        """cutscene <id> … end: a scene that a background routine triggers with
        play <id> (e.g. the warnings of the pirates when you get too close to the
        rat). It runs in the foreground like a verb script."""
        if len(args) != 1:
            raise ln.err("Syntax: cutscene <id>")
        cid = self.new_id(self.g.cutscenes, ln, args[0], "cutscene")
        body, _ = self.block(("end",))
        self.g.cutscenes[cid] = (ln, body)

    def top_number(self, ln, args):
        """number <variable> original <nr>: variable whose value texts of the
        original insert as a number with code 4 (<4:nr>) (e.g. the money)."""
        if len(args) != 3 or args[1] != "original":
            raise ln.err("Syntax: number <variable> original <nr>")
        if args[0] in self.g.numbers:
            raise ln.err(f"number {args[0]} given twice")
        self.g.numbers[args[0]] = self.num(ln, args[2])

    def top_string(self, ln, args):
        """string <id> original <nr>: string variable that texts of the original
        insert with code 7 (<7:nr>), e.g. the name the lookout
        remembers. Content via setstring/setchar."""
        if len(args) != 3 or args[1] != "original":
            raise ln.err("Syntax: string <id> original <nr>")
        sid = self.new_id(self.g.strings, ln, args[0], "string")
        self.g.strings[sid] = {"original": self.num(ln, args[2]), "slot": len(self.g.strings), "line": ln}

    def top_card(self, ln, args):
        """card <id> original <room> [threshold N] [music <id>]: full screen from
        an original room (chapter card). The content is lettering: cropped to
        the bright area, fitted to 128×64, threshold on the brightest
        colour component (lettering in blue or similar stays legible). The image comes from
        each language version separately – the text is drawn in."""
        if len(args) < 3 or args[1] != "original":
            raise ln.err("Syntax: card <id> original <room> [threshold N] [music <id>]")
        cid = self.new_id(self.g.cards, ln, args[0], "card")
        opts = self.keyvals(ln, args[3:], {"threshold": 1, "music": 1})
        self.g.cards[cid] = {"room": self.num(ln, args[2]), "threshold": opts.get("threshold", [50])[0],
                             "music": opts.get("music", [None])[0], "line": ln}

    # ---- Greyscale -------------------------------------------------------

    GREY_SYNTAX = ("Syntax: greyscale title | card <id> | room <id>, containing the lines\n"
                   "  layer [from X] [tone options]\n"
                   "  subject hue <name> | polygon X Y X Y … [tone options] [min N] [outline]\n"
                   "tone options: channel C | weights R G B, tone S W | tone auto, contrast K, "
                   "gamma G, max N, dither")

    def top_greyscale(self, ln, args):
        """Greyscale version of an image (original.to_grey). Stands on its own
        so that title, cards and rooms use the same description."""
        if args[:1] == ["title"] and len(args) == 1:
            key = ("title", None)
        elif len(args) == 2 and args[0] in ("card", "room"):
            key = (args[0], args[1])
        else:
            raise ln.err(self.GREY_SYNTAX)
        if key in self.g.greyscale:
            raise ln.err(f"greyscale {' '.join(args)} given twice")
        spec = {"layers": [], "subjects": [], "line": ln}
        while True:
            sub = self.next()
            if sub is None:
                raise ln.err("greyscale: 'end' missing")
            kw, *a = sub.tokens
            if kw == "end" and not a:
                break
            if kw == "layer":
                spec["layers"].append(self.grey_options(sub, a, layer=True))
            elif kw == "subject":
                spec["subjects"].append(self.grey_options(sub, a, layer=False))
            else:
                raise sub.err(self.GREY_SYNTAX)
        if not spec["layers"]:
            raise ln.err("greyscale needs at least one layer line")
        edges = [layer.get("from", 0) for layer in spec["layers"]]
        if len(set(edges)) != len(edges):
            raise ln.err("two layers with the same edge (from)")
        if 0 not in edges:
            raise ln.err("one layer must start at the left edge (without from)")
        self.g.greyscale[key] = spec

    def grey_options(self, ln, toks, layer):
        out = {}
        i = 0
        if not layer:
            if toks[:1] == ["hue"] and len(toks) >= 2:
                if toks[1] not in orig.HUES:
                    raise ln.err(f"hue: {'/'.join(orig.HUES)}")
                out["mask"] = ("hue", toks[1])
                i = 2
            elif toks[:1] == ["polygon"]:
                j = 1
                while j < len(toks) and re.fullmatch(r"-?\d+(\.\d+)?", toks[j]):
                    j += 1
                nums = [self.real(ln, v) for v in toks[1:j]]
                if len(nums) < 6 or len(nums) % 2:
                    raise ln.err("polygon needs at least three points (X Y …)")
                out["mask"] = ("polygon", list(zip(nums[::2], nums[1::2])))
                i = j
            else:
                raise ln.err("subject needs hue <name> or polygon X Y …")
        while i < len(toks):
            k = toks[i]
            def take(n):
                vals = toks[i + 1:i + 1 + n]
                if len(vals) != n:
                    raise ln.err(f"'{k}' needs {n} value(s)")
                return vals
            if k in out:
                raise ln.err(f"'{k}' given twice")
            if k == "from" and layer:
                out[k] = self.real(ln, take(1)[0])
            elif k == "channel":
                if take(1)[0] not in orig.CHANNELS:
                    raise ln.err(f"channel: {'/'.join(orig.CHANNELS)}")
                out[k] = toks[i + 1]
            elif k == "weights":
                out[k] = tuple(self.real(ln, v) for v in take(3))
            elif k == "tone":
                if toks[i + 1:i + 2] == ["auto"]:
                    out[k] = "auto"
                    i += 2
                    continue
                black, white = (self.real(ln, v) for v in take(2))
                if not 0 <= black < white:
                    raise ln.err("tone: 0 ≤ black < white")
                out[k] = (black, white)
            elif k in ("contrast", "gamma"):
                out[k] = self.real(ln, take(1)[0])
                if out[k] < 0 or (k == "gamma" and out[k] == 0):
                    raise ln.err(f"{k} must be positive")
            elif k in ("max", "min"):
                if k == "min" and layer:
                    raise ln.err("min only with subject")
                out[k] = self.num(ln, take(1)[0])
                if not 0 <= out[k] <= orig.GREY_PLANES:
                    raise ln.err(f"{k}: 0…{orig.GREY_PLANES}")
            elif k == "dither" and layer:
                out[k] = True
                i += 1
                continue
            elif k == "outline" and not layer:
                out[k] = True
                i += 1
                continue
            else:
                raise ln.err(f"unknown option '{k}'\n{self.GREY_SYNTAX}")
            i += 1 + {"weights": 3, "tone": 2}.get(k, 1)
        if "channel" in out and "weights" in out:
            raise ln.err("channel and weights are mutually exclusive")
        if out.get("min", 0) > out.get("max", orig.GREY_PLANES):
            raise ln.err("min greater than max")
        return out

    def top_title(self, ln, args):
        syntax = ('Syntax: title "<png>" [music <id>] | title original <room> [overlay <object> X Y] '
                  'crop X0 Y0 X1 Y1 [tone S W] [contrast K] [music <id>]')
        if not args:
            raise ln.err(syntax)
        if args[0] != "original":
            if len(args) not in (1, 3) or (len(args) == 3 and args[1] != "music"):
                raise ln.err(syntax)
            self.g.title = args[0]
            self.g.title_music = (args[2], ln) if len(args) == 3 else None
            return
        if len(args) < 2:
            raise ln.err(syntax)
        opts = self.keyvals(ln, args[2:], {"overlay": 3, "crop": 4, "tone": 2, "contrast": 1, "music": 1})
        if "crop" not in opts:
            raise ln.err("title original needs crop X0 Y0 X1 Y1")
        source = self.g.original_room(self.num(ln, args[1]), ln)
        img = source.image()
        if "overlay" in opts:
            number, x, y = opts["overlay"]
            try:
                img.paste(source.object_image(number), (x, y))
            except Exception as e:
                raise ln.err(f"overlay: {e}")
        x0, y0, x1, y1 = opts["crop"]
        if not (0 <= x0 < x1 <= img.width and 0 <= y0 < y1 <= img.height):
            raise ln.err("crop lies outside the image")
        width = orig.scaled(x1 - x0, 64 / (y1 - y0))
        if width > 128:
            raise ln.err(f"crop is too wide: becomes {width} px instead of at most 128")
        black, white = opts.get("tone", [30, 95])
        mono = orig.to_mono(img.crop((x0, y0, x1, y1)), (width, 64), black, white,
                            opts.get("contrast", [1.0])[0])
        canvas = Image.new("RGBA", (128, 64), (0, 0, 0, 255))
        canvas.paste(mono, ((128 - width) // 2, 0))
        self.g.title = ("pil", "title", canvas)
        self.g.title_source = {"image": img.crop((x0, y0, x1, y1)), "width": width, "tone": (black, white)}
        self.g.title_music = (opts["music"][0], ln) if "music" in opts else None

    # ---- Helpers ---------------------------------------------------------

    def num(self, ln, v):
        try:
            return int(v, 0)
        except ValueError:
            raise ln.err(f"number expected, not '{v}'")

    def real(self, ln, v):
        try:
            return float(v)
        except ValueError:
            raise ln.err(f"number expected, not '{v}'")

    def keyvals(self, ln, toks, arity, words=()):
        out = {}
        i = 0
        while i < len(toks):
            k = toks[i]
            if k not in arity:
                raise ln.err(f"unknown option '{k}'")
            n = arity[k]
            if n is None:  # any number of numbers up to the next option
                n = 0
                while i + 1 + n < len(toks) and toks[i + 1 + n] not in arity:
                    n += 1
                if not n:
                    raise ln.err(f"'{k}' needs at least one value")
            vals = toks[i + 1:i + 1 + n]
            if len(vals) != n:
                raise ln.err(f"'{k}' needs {n} value(s)")
            out[k] = [v if k in ("image", "face", "music", "object") or k in words else
                      self.real(ln, v) if k in ("contrast", "scale", "states") else self.num(ln, v) for v in vals]
            i += 1 + n
        return out

    def place(self, ln, a, room):
        # place <obj> at x y [w h] [image …] walkto x y face dir
        # place <obj> original <nr> [image …] [walkto x y] [face dir]
        # place decor at x y costume|image … – image only, no hotspot
        syntax = ('Syntax: place <object> at x y [w h] [image "<png>" frames N speed N] walkto x y face <dir> '
                  '| place <object> original <nr> [walkto x y] [face <dir>]')
        if len(a) < 3 or a[1] not in ("at", "original"):
            raise ln.err(syntax)
        obj = a[0]
        w = h = None
        walk = face = None
        dx = 0   # offset of narrow rooms (only for coordinates from the original)
        if a[1] == "original":
            dx = room["dx"]
            if not room["original"]:
                raise ln.err("place … original only in a room from the original data")
            number = self.num(ln, a[2])
            source = next((o for o in room["original"].objects if o.number == number), None)
            if source is None:
                raise ln.err(f"object {number} does not exist in original room {room['original'].number}")
            s = room["scale"]
            x, y = orig.scaled(source.x, s), orig.scaled(source.y, s)
            w, h = max(1, orig.scaled(source.width, s)), max(1, orig.scaled(source.height, s))
            walk = [orig.scaled(source.walk_x, s) + dx, orig.scaled(source.walk_y, s)]
            face = orig.SCUMM_DIRS[source.direction & 3]
            rest = a[3:]
        else:
            if len(a) < 4:
                raise ln.err(syntax)
            x, y = self.num(ln, a[2]), self.num(ln, a[3])
            rest = a[4:]
            if len(rest) >= 2 and rest[0].isdigit():
                w, h = self.num(ln, rest[0]), self.num(ln, rest[1])
                rest = rest[2:]
        opts = self.keyvals(ln, rest, {"image": 1, "frames": 1, "speed": 1, "walkto": 2, "face": 1,
                                       "costume": 1, "states": None, "palette": 1, "dark": 1, "outline": 1,
                                       "picture": 0, "door": 0, "direct": 0})
        image = opts.get("image", [None])[0]
        frames = opts.get("frames", [1])[0]
        # Image from the background: background per state and cutout, so that
        # compile() computes the greyscale with the room's greyscale block
        grey_from = None
        grey_sprite = None  # character as decor: greyscale from the costume
        backdrop = 0
        if "picture" in opts:
            # Pickable item: its original image, cut out of the
            # background with the object overlaid. The background itself is converted
            # without it; if you take it, the image disappears.
            if a[1] != "original":
                raise ln.err("picture only with place … original <nr>")
            src = room["original"]
            comp = room["composite"].copy()
            comp.paste(self.object_image(ln, src, number), self.object_pos(ln, src, number))
            mono = orig.to_mono(comp, *room["tone"])
            image = ("pil", f"room{src.number}_obj{number}", mono.crop((x, y, x + w, y + h)))
            grey_from = {"key": f"room{src.number}_obj{number}", "box": (x, y, w, h), "states": [comp]}
        if "door" in opts:
            # Door: state 0 = closed (like the background), 1 = open (object image of the
            # original overlaid) – two images, one per state.
            if a[1] != "original":
                raise ln.err("door only with place … original <nr>")
            src = room["original"]
            comp = room["composite"].copy()
            closed = orig.to_mono(comp, *room["tone"]).crop((x, y, x + w, y + h))
            grey_from = {"key": f"room{src.number}_door{number}", "box": (x, y, w, h),
                         "states": [comp.copy()]}
            comp.paste(self.object_image(ln, src, number), self.object_pos(ln, src, number))
            opened = orig.to_mono(comp, *room["tone"]).crop((x, y, x + w, y + h))
            grey_from["states"].append(comp)
            strip = Image.new("RGBA", (w * 2, h))
            strip.paste(closed, (0, 0))
            strip.paste(opened, (w, 0))
            image = ("pil", f"room{src.number}_door{number}", strip, (w, h))
            backdrop = 1  # state 0 is the cutout of the background itself
        if "costume" in opts:
            # at x y is the foot point here; the image stands centred above it.
            image, grey_sprite, frames, fw, fh = self.costume_place_image(
                ln, opts["costume"][0], opts.get("states", [room["scale"]]),
                opts.get("palette", [38])[0], opts.get("dark", [45])[0], opts.get("outline", [1])[0])
            x, y = x - fw // 2, y - fh + 1
        if "walkto" in opts:
            walk = opts["walkto"]
        if "direct" in opts:
            # Verb scripts start on click without the player character walking
            # there first; the script makes it walk itself (like r28.s203, which
            # looks for the cook right away when clicking the kitchen door).
            if "walkto" in opts:
                raise ln.err("direct and walkto are mutually exclusive")
            walk = [WALK_DIRECT, 0]
        if "face" in opts:
            if opts["face"][0] not in DIRS:
                raise ln.err(f"face: {'/'.join(DIRS)}")
            face = DIRS[opts["face"][0]]
        if obj == "decor":
            if not image:
                raise ln.err("place decor needs an image (image, costume or picture)")
            walk, face = walk or [0, 0], 0 if face is None else face
        elif walk is None or face is None:
            raise ln.err("place needs walkto and face")
        return {"object": obj, "x": x + dx, "y": y, "w": w, "h": h, "image": image, "grey_from": grey_from,
                "backdrop": backdrop, "grey_sprite": grey_sprite,
                "frames": frames, "speed": opts.get("speed", [8])[0],
                "walk": walk, "face": face, "line": ln}

    def object_image(self, ln, source, number):
        import scumm_v4
        try:
            return source.object_image(number)
        except scumm_v4.ScummError as e:
            raise ln.err(str(e))

    def object_pos(self, ln, source, number):
        o = next((o for o in source.objects if o.number == number), None)
        if o is None:
            raise ln.err(f"object {number} does not exist in original room {source.number}")
        return o.x, o.y

    def text_ref(self, ln, tok):
        """Game texts are not in the repo: only references to the copy of the game
        (or ui.<key> for the engine's own texts)."""
        m = UI_REF.fullmatch(tok)
        if m:
            if m.group(1) not in UI_TEXT["en"]:
                raise ln.err(f"unknown engine text {tok!r} ({', '.join(UI_TEXT['en'])})")
            return tok
        if not REF.fullmatch(tok):
            raise ln.err(f"give texts as a reference to the original data (e.g. r38.s203#1, o498.name), "
                         f"not {tok!r}; list: tools/scumm_text.py <copy>")
        return tok

    def ref(self, table, ln, ident, what):
        if ident not in table:
            raise ln.err(f"unknown {what} '{ident}'")
        return ident

    # ---- Script blocks ---------------------------------------------------

    def block(self, terminators):
        """Reads statements up to one of the terminators. Returns (AST, terminator line)."""
        stmts = []
        while True:
            ln = self.next()
            if ln is None:
                raise CompileError(f"end of file: expected {' / '.join(terminators)}")
            kw = ln.tokens[0]
            if kw in terminators:
                return stmts, ln
            if kw == "if":
                cond = self.cond(ln, ln.tokens[1:])
                then, term = self.block(("else", "end"))
                other = []
                if term.tokens[0] == "else":
                    other, _ = self.block(("end",))
                stmts.append(("if", ln, cond, then, other))
            elif kw == "choose":
                stmts.append(self.choose(ln))
            elif kw == "repeat":
                body, _ = self.block(("end",))
                stmts.append(("repeat", ln, body))
            elif kw == "random":
                stmts.append(self.random_block(ln))
            else:
                if kw == "setstring" and len(ln.tokens) == 3:
                    self.text_ref(ln, ln.tokens[2])
                    self.g.string_refs.setdefault(ln.tokens[1], []).append(ln.tokens[2])
                if kw == "say" and len(ln.tokens) == 3:
                    self.text_ref(ln, ln.tokens[2])
                stmts.append(("cmd", ln, ln.tokens))

    def cond(self, ln, toks):
        """Condition: sub-conditions joined with "and"; list of atoms."""
        atoms, part = [], []
        for t in toks + ["and"]:
            if t == "and":
                if not part:
                    raise ln.err("condition: 'and' without a sub-condition")
                atoms.append(self.cond_atom(ln, part))
                part = []
            else:
                part.append(t)
        return atoms

    def cond_atom(self, ln, toks):
        neg = bool(toks) and toks[0] == "not"
        if neg:
            toks = toks[1:]
        if len(toks) == 2 and toks[0] == "has":
            return ("has", toks[1], neg)
        if len(toks) == 2 and toks[1] == "open":
            return ("open", toks[0], neg)
        if len(toks) == 2 and toks[0] == "hover":
            return ("hover", toks[1], neg)
        if len(toks) == 1:
            return ("flag", toks[0], neg)
        if len(toks) == 3 and toks[1] in VAR_CMP:
            try:
                return ("var", (toks[0], toks[1], int(toks[2], 0)), neg)
            except ValueError:
                raise ln.err("<variable> =|<|> <number>")
        if len(toks) == 4 and toks[1] in ("x", "y") and toks[2] in ("<", ">"):
            try:
                return ("pos", (toks[0], toks[1], toks[2], int(toks[3], 0)), neg)
            except ValueError:
                raise ln.err("<actor> x|y <|> <number>")
        raise ln.err("condition: [not] <flag> | [not] has <object> | [not] <object> open | [not] hover <object> | "
                     "[not] <actor> x|y <|> <number> | [not] <variable> =|<|> <number>, several joined with 'and'")

    def random_block(self, ln):
        """random / case [weight] … / end: one of the cases, uniformly distributed
        or by weight (like getRandomNr in the original; an empty case is
        allowed). The engine picks an entry of its jump table, a case
        with weight n appears there n times."""
        if len(ln.tokens) != 1:
            raise ln.err("Syntax: random, below it 'case [weight]' per case, then 'end'")

        def weight(line):
            if line is None or line.tokens[0] != "case" or len(line.tokens) > 2:
                raise ln.err("random: entries start with 'case [weight]'")
            w = self.num(line, line.tokens[1]) if len(line.tokens) == 2 else 1
            if not 0 < w < 256:
                raise line.err("case: weight 1…255")
            return w

        w = weight(self.next())
        cases = []
        while True:
            body, term = self.block(("case", "end"))
            cases.append((w, body))
            if term.tokens[0] == "end":
                break
            w = weight(term)
        if sum(w for w, _ in cases) > 255:
            raise ln.err("random: weights add up to at most 255")
        if len(cases) < 2:
            raise ln.err("random needs at least two cases")
        return ("random", ln, cases)

    def choose(self, ln):
        options = []
        opt_line = self.next()
        while opt_line is not None and opt_line.tokens[0] == "option":
            t = opt_line.tokens
            if len(t) < 2:
                raise opt_line.err("Syntax: option <text> [if <condition>]")
            self.text_ref(opt_line, t[1])
            cond = None
            if len(t) > 2:
                if t[2] != "if":
                    raise opt_line.err("after the option text only 'if …'")
                cond = self.cond(opt_line, t[3:])
            body, term = self.block(("option", "end"))
            options.append({"text": t[1], "cond": cond, "body": body, "line": opt_line})
            if term.tokens[0] == "end":
                break
            self.i -= 1  # 'option' belongs to the next round
            opt_line = self.next()
        else:
            raise ln.err("choose needs at least one 'option' and an 'end'")
        if len(options) > 255:
            raise ln.err("at most 255 options per choose")
        return ("choose", ln, options)


# --------------------------------------------------------------------------
# Code generator
# --------------------------------------------------------------------------

class Compiler:
    def __init__(self, game):
        self.g = game
        self.b = Blob()
        self.in_entry = False
        self.in_routine = False
        self.strings = {}         # text → label, new per language
        self.label_no = 0
        self.images = {}
        self.grey_previews = {}  # key → grey image for viewing (--preview)
        self.src = None           # TextSource of the language currently being translated
        self.text_max = 0         # longest speech bubble/option (engine's text buffer)
        self.max_visible = 1      # options visible at once (engine's choice buffer)
        self.lang_ui = {}         # language → labels of the engine texts (UI_TEXT)

    def label(self, hint):
        self.label_no += 1
        return f"{hint}_{self.label_no}"

    def L(self, name):
        """Label of a language-dependent part (header, verbs, scripts, texts)."""
        return f"{self.src.code}:{name}"

    @staticmethod
    def encode_text(text):
        """Text → bytes for the engine: CP437, placeholder slots as one byte
        (slot + 1, see scumm_text.SLOT_BASE)."""
        enc = bytearray()
        for ch in text:
            if scumm_text.SLOT_BASE <= ord(ch) < scumm_text.SLOT_BASE + 0xFF:
                enc.append(ord(ch) - scumm_text.SLOT_BASE + 1)
                continue
            try:
                enc += ch.encode("cp437")
            except UnicodeEncodeError as e:
                raise CompileError(f"character not in the Arduboy font (CP437): {text!r} ({e})")
        if 0 in enc:
            raise CompileError(f"NUL in string: {text!r}")
        return bytes(enc)

    def string(self, text):
        """Creates a (null-terminated) string; duplicates are shared."""
        self.encode_text(text)   # check early, with line reference at the caller
        if text not in self.strings:
            self.strings[text] = self.label("str")
        return self.strings[text]

    def text_line(self, ln, ref):
        """Single-line game text (verb, name) of the current language as a string."""
        try:
            return self.string(self.src.line(ref))
        except TextError as e:
            raise (ln.err(str(e)) if ln else CompileError(str(e)))

    def bubbles(self, ln, ref):
        """Speech bubbles of a game text in the current language."""
        m = UI_REF.fullmatch(ref)
        if m:
            text = UI_TEXT.get(self.src.code, UI_TEXT["en"])[m.group(1)]
            lines = wrap(text, TEXT_COLS)
            return ["\n".join(lines[i:i + TEXT_ROWS]) for i in range(0, len(lines), TEXT_ROWS)]
        try:
            return self.src.bubbles(ref, TEXT_COLS, TEXT_ROWS)
        except TextError as e:
            raise ln.err(str(e))

    def card_image(self, cid):
        """Image of the card in the language self.src: (label, label of the
        greyscale or NONE24)."""
        c = self.g.cards[cid]
        room = self.g.original_room_of(self.src.dir, c["room"], c["line"])
        rgb = np.asarray(room.image().convert("RGB")).max(axis=2)
        ys, xs = np.nonzero(rgb > c["threshold"])
        if not len(xs):
            raise c["line"].err(f"room {c['room']}: nothing brighter than {c['threshold']}")
        h_img, w_img = rgb.shape
        x0, y0 = max(0, xs.min() - 2), max(0, ys.min() - 2)
        x1, y1 = min(w_img, xs.max() + 3), min(h_img, ys.max() + 3)
        scale = min(128 / (x1 - x0), 64 / (y1 - y0))
        size = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale)))
        bright = Image.fromarray(rgb.astype(np.uint8)).crop((x0, y0, x1, y1)).resize(size, Image.BOX)
        mono = bright.point(lambda v: 255 if v > c["threshold"] else 0).convert("L")
        canvas = Image.new("RGBA", (128, 64), (0, 0, 0, 255))
        at = ((128 - size[0]) // 2, (64 - size[1]) // 2)
        canvas.paste(Image.merge("RGBA", (mono, mono, mono, Image.new("L", size, 255))), at)
        label = self.image(("pil", f"card_{cid}_{self.src.code}", canvas), c["line"])["label"]
        spec = self.g.greyscale.get(("card", cid))
        if not spec:
            return label, NONE24
        layer = spec["layers"][0]
        levels = orig.card_grey(np.asarray(bright, dtype=float), c["threshold"],
                                layer.get("gamma", 1.0), layer.get("dither", False))
        full = np.zeros((64, 128), dtype=np.uint8)
        full[at[1]:at[1] + size[1], at[0]:at[0] + size[0]] = levels
        return label, self.grey_image(f"card_{cid}_{self.src.code}_grey", [full], spec["line"])

    def grey_image(self, key, frames, ln, mask=False, opaque=None):
        """Store greyscale (one array with levels 0–3 per frame of the 1-bit image) as
        an image: frame f becomes frames 3f, 3f+1, 3f+2 (plane 0–2).
        opaque: the opaque pixels per frame (characters), otherwise all."""
        h, w = frames[0].shape
        planes = orig.GREY_PLANES
        strip = Image.new("RGBA", (w * planes * len(frames), h))
        for i, levels in enumerate(frames):
            for p, plane in enumerate(orig.grey_planes(levels, opaque[i] if opaque else None)):
                strip.paste(plane, ((i * planes + p) * w, 0))
        self.grey_previews[key] = orig.grey_preview(np.hstack(frames))
        return self.image(("pil", key, strip, (w, h)), ln, mask)["label"]

    def figure_grey(self, sprite, ln):
        """Store a character in greyscale (("grey", key, strip, frame width)
        from original.costume_frames); None → NONE24."""
        if not sprite:
            return NONE24
        _, key, strip, fw = sprite
        frames = orig.figure_levels(strip, fw)
        return self.grey_image(key, [lv for lv, _ in frames], ln, mask=True, opaque=[op for _, op in frames])

    def grey_levels(self, spec, img, size, tone, channel):
        try:
            return orig.to_grey(img, size, spec["layers"], spec["subjects"], tone, channel)
        except ValueError as e:
            raise spec["line"].err(str(e))

    def room_grey(self, rid, r):
        """Greyscale background of a room (label or NONE24)."""
        spec = self.g.greyscale.get(("room", rid))
        if not spec:
            return NONE24
        size, black, white, _, channel = r["tone"]
        levels = self.grey_levels(spec, r["composite"], size, (black, white), channel)
        if r["dx"]:
            padded = np.zeros((levels.shape[0], 128), dtype=np.uint8)
            padded[:, r["dx"]:r["dx"] + levels.shape[1]] = levels
            levels = padded
        return self.grey_image(f"room{r['original'].number}_grey", [levels], spec["line"])

    def place_grey(self, rid, r, p):
        """Greyscale of a door or an item: cutout of the background
        per state, computed like the room's background."""
        spec = self.g.greyscale.get(("room", rid))
        if not spec or not p["grey_from"]:
            return NONE24
        size, black, white, _, channel = r["tone"]
        x, y, w, h = p["grey_from"]["box"]
        frames = [self.grey_levels(spec, comp, size, (black, white), channel)[y:y + h, x:x + w]
                  for comp in p["grey_from"]["states"]]
        return self.grey_image(p["grey_from"]["key"] + "_grey", frames, p["line"], mask=True)

    def title_grey(self):
        spec = self.g.greyscale.get(("title", None))
        if not spec:
            return NONE24
        src = self.g.title_source
        levels = self.grey_levels(spec, src["image"], (src["width"], 64), src["tone"], "luminance")
        full = np.zeros((64, 128), dtype=np.uint8)
        at = (128 - src["width"]) // 2
        full[:, at:at + src["width"]] = levels
        return self.grey_image("title_grey", [full], spec["line"])

    def check_greyscale(self):
        g = self.g
        for (kind, ident), spec in g.greyscale.items():
            ln = spec["line"]
            if kind == "room":
                if ident not in g.rooms:
                    raise ln.err(f"unknown room '{ident}'")
                if not g.rooms[ident]["original"]:
                    raise ln.err("greyscale room only for rooms from the original data")
            elif kind == "card":
                if ident not in g.cards:
                    raise ln.err(f"unknown card '{ident}'")
                if spec["subjects"] or len(spec["layers"]) != 1 or \
                        set(spec["layers"][0]) - {"gamma", "dither"}:
                    raise ln.err("greyscale card: exactly one layer line, only gamma and dither "
                                 "(the card is lettering, its tone range follows from that)")
            elif not g.title_source:
                raise ln.err("greyscale title only for title original …")

    def image(self, src, ln, mask=False):
        """Image from a PNG file (path) or from the original data
        (("pil", key, image[, frame size])); identical sources are only
        stored once."""
        key = (src[1] if isinstance(src, tuple) else src, mask)
        if key not in self.images:
            if isinstance(src, tuple):  # ("pil", key, image[, frame size])
                data, w, h, frames = encode_pil(src[2], src[1], src[3] if len(src) > 3 else None, mask)
            else:
                path = self.g.base / src
                if not path.exists():
                    msg = f"image missing: {src}"
                    raise ln.err(msg) if ln else CompileError(msg)
                data, w, h, frames = encode_image(path, mask)
            self.images[key] = {"label": self.label("img"), "data": data, "w": w, "h": h,
                                "frames": frames, "source": src}
        return self.images[key]

    # ---- References ----

    def var(self, ln, name):
        err = ln.err if ln else CompileError
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            raise err(f"invalid variable name '{name}'")
        if name in self.g.flags:
            raise err(f"'{name}' is already a flag")
        if name not in self.g.vars:
            if len(self.g.vars) >= 255:
                raise err("more than 255 variables")
            self.g.vars[name] = len(self.g.vars)
        return self.g.vars[name]

    def flag(self, name):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            raise CompileError(f"invalid flag name '{name}'")
        if name in self.g.vars:
            raise CompileError(f"'{name}' is already a variable")
        if name not in self.g.flags:
            if len(self.g.flags) >= 255:
                raise CompileError("more than 255 flags")
            self.g.flags[name] = len(self.g.flags)
        return self.g.flags[name]

    def obj(self, ln, name):
        if name not in self.g.objects:
            raise ln.err(f"unknown object '{name}'")
        return self.g.objects[name]["index"]

    def actor(self, ln, name):
        if name == "narrator":
            return NONE8
        if name not in self.g.actors:
            raise ln.err(f"unknown actor '{name}'")
        return self.g.actors[name]["index"]

    def text_buffer(self):
        if self.text_max + 1 > 255:
            raise CompileError(f"text with {self.text_max} characters too long for the text buffer (at most 254)")
        return self.text_max + 1

    def shown_len(self, text):
        """Length of a text as the engine outputs it: placeholders with the
        longest content of the variables."""
        n = len(text)
        for code, width in self.src.widths().items():
            n += text.count(code) * (width - 2)
        return n

    # ---- Call depth (engine's return stack) ----

    def _children(self, stmts):
        for st in stmts:
            kind = st[0]
            if kind == "if":
                yield from self._children(st[3])
                yield from self._children(st[4] or [])
            elif kind == "choose":
                for opt in st[2]:
                    yield from self._children(opt["body"])
            elif kind == "repeat":
                yield from self._children(st[2])
            elif kind == "random":
                for _, body in st[2]:
                    yield from self._children(body)
            elif kind == "cmd":
                yield st

    def depth(self, stmts, path=()):
        """Return slots a script needs at most: call and ROOM (the
        entry script of the new room) one each, plus whatever is called in them."""
        need = 0
        for _, ln, toks in self._children(stmts):
            if toks[0] == "call" and len(toks) == 2 and toks[1] in self.g.subs:
                if toks[1] in path:
                    raise ln.err(f"subroutine '{toks[1]}' calls itself")
                need = max(need, 1 + self.depth(self.g.subs[toks[1]][1], path + (toks[1],)))
            elif toks[0] == "room":
                need = max(need, 1 + self.entry_depth)
        return need

    def changes_room(self, stmts, path=()):
        for _, ln, toks in self._children(stmts):
            if toks[0] == "room":
                return True
            if toks[0] == "call" and len(toks) == 2 and toks[1] in self.g.subs and toks[1] not in path:
                if self.changes_room(self.g.subs[toks[1]][1], path + (toks[1],)):
                    return True
        return False

    ROUTINES = 2   # background slots of the engine (Script.cpp)

    def started(self, stmts, path=()):
        """Background routines a script (including subroutines) can start."""
        out = set()
        for _, ln, toks in self._children(stmts):
            if toks[0] == "start" and len(toks) == 2:
                out.add(toks[1])
            elif toks[0] == "call" and len(toks) == 2 and toks[1] in self.g.subs and toks[1] not in path:
                out |= self.started(self.g.subs[toks[1]][1], path + (toks[1],))
        return out

    def check_routines(self):
        """A room change ends all background routines; so in a room at most those run
        that its entry script, the verbs of its objects or
        an inventory object start. These must not be more than ROUTINES."""
        g = self.g
        placed = {}
        for rid, r in g.rooms.items():
            placed[rid] = {p["object"] for p in r["places"]}
        anywhere = set()
        for oid, o in g.objects.items():
            if not any(oid in objs for objs in placed.values()):
                for h in o["verbs"]:
                    anywhere |= self.started(h["body"])
        for rid, r in g.rooms.items():
            names = set(anywhere) | self.started(r["entry"])
            for oid in placed[rid]:
                if oid in g.objects:
                    for h in g.objects[oid]["verbs"]:
                        names |= self.started(h["body"])
            if len(names) > self.ROUTINES:
                raise r["line"].err(f"room {rid}: up to {len(names)} routines at once "
                                    f"({', '.join(sorted(names))}), the engine has {self.ROUTINES} slots")

    def call_depth(self):
        g = self.g
        for rid, r in g.rooms.items():
            if self.changes_room(r["entry"]):
                raise r["line"].err(f"room {rid}: entry scripts must not change the room "
                                    f"(not even via a subroutine)")
        self.entry_depth = 0
        self.entry_depth = max([self.depth(r["entry"]) for r in g.rooms.values()], default=0)
        bodies = [b for b in g.defaults.values()]
        bodies += [h["body"] for o in g.objects.values() for h in o["verbs"]]
        bodies += [body for _, body in g.routines.values()]
        bodies += [body for _, body in g.cutscenes.values()]
        bodies += [body for _, body in g.subs.values()]
        if not isinstance(g.start_room, tuple):
            bodies.append(g.start_room)
        else:
            bodies.append([])
        self.check_routines()
        need = max([self.depth(b) for b in bodies] + [1 + self.entry_depth])
        if need > 8:
            raise CompileError(f"subroutines nested too deeply ({need} levels, at most 8)")
        return need

    def string_var(self, ln, sid):
        if sid not in self.g.strings:
            raise ln.err(f"unknown string '{sid}' (string <id> original <nr>)")
        return sid

    def string_widths(self, src):
        """Longest content per string variable in this language (setstring)."""
        out = {}
        for sid, v in self.g.strings.items():
            refs = self.g.string_refs.get(sid)
            if not refs:
                raise v["line"].err(f"string '{sid}' is never set (setstring)")
            try:
                out[v["original"]] = (v["slot"], max(len(src.line(r)) for r in refs))
            except TextError as e:
                raise v["line"].err(str(e))
        return out

    def jump_unless(self, ln, cond, target):
        """Jumps to target as soon as a sub-condition is not met."""
        for kind, name, neg in cond:
            n = COND_NOT if neg else 0
            if kind == "var":
                var, cmp, value = name
                if not 0 <= value < 256:
                    raise ln.err("variables have values 0…255")
                self.op("JUNLESSV", VAR_CMP[cmp] | n, self.var(ln, var), value, target)
            elif kind == "pos":
                actor, axis, cmp, value = name
                if self.actor(ln, actor) == NONE8:
                    raise ln.err("position condition needs an actor")
                k = (POS_Y if axis == "y" else 0) | (POS_GT if cmp == ">" else 0) | n
                self.op("JUNLESSPOS", k, self.actor(ln, actor), value, target)
            elif kind in ("has", "open", "hover"):
                k = {"has": COND_HAS, "open": COND_OPEN, "hover": COND_HOVER}[kind]
                self.op("JUNLESS", k | n, self.obj(ln, name), target)
            else:
                self.op("JUNLESS", COND_FLAG | n, self.flag(name), target)

    # ---- Scripts ----

    def op(self, name, *operands):
        self.b.put("u8", OP[name])
        types = dict(OPCODES)[name]
        if len(types) != len(operands):
            raise CompileError(f"internal error: {name} expects {len(types)} operands")
        for t, v in zip(types, operands):
            self.b.put(t, v)

    def script(self, stmts, label):
        self.b.mark(label)
        self.stmts(stmts)
        self.op("END")

    def stmts(self, stmts):
        for st in stmts:
            getattr(self, f"st_{st[0]}")(*st[1:])

    def st_if(self, ln, cond, then, other):
        l_else, l_end = self.label("else"), self.label("endif")
        self.jump_unless(ln, cond, l_else if other else l_end)
        self.stmts(then)
        if other:
            self.op("JMP", l_end)
            self.b.mark(l_else)
            self.stmts(other)
        self.b.mark(l_end)

    def st_repeat(self, ln, body):
        """repeat … end: endless loop (background routines); left only
        through stop or a room change."""
        top = self.label("repeat")
        self.b.mark(top)
        self.stmts(body)
        self.op("JMP", top)

    def st_random(self, ln, cases):
        l_end = self.label("random_end")
        targets = [self.label("case") for _ in cases]
        self.op("RANDOM", sum(w for w, _ in cases))
        for (w, _), t in zip(cases, targets):
            for _ in range(w):
                self.b.put("u24", t)
        for (_, body), t in zip(cases, targets):
            self.b.mark(t)
            self.stmts(body)
            self.op("JMP", l_end)
        self.b.mark(l_end)

    @staticmethod
    def visible_bound(options):
        """Upper bound on how many options can be visible at once:
        each sub-condition (without "not") as a free truth value, all
        assignments tried. Dependencies between sub-conditions
        (such as two comparisons of the same variable) are ignored –
        that only makes the bound larger, never too small."""
        def key(atom):
            kind, name, _ = atom
            return (kind, repr(name))
        atoms = sorted({key(a) for o in options for a in (o["cond"] or [])})
        if len(atoms) > 16:
            return len(options)
        best = 0
        for bits in range(1 << len(atoms)):
            value = {k: bool(bits >> i & 1) for i, k in enumerate(atoms)}
            n = sum(all(value[key(a)] != a[2] for a in (o["cond"] or [])) for o in options)
            best = max(best, n)
        return best

    def st_choose(self, ln, options):
        """Dialogue choice as code: CHOICES, per option condition + OPTION, ASK.
        After each option it goes back to the choice (conditions checked
        again), until an option executes 'done' or none are left."""
        if self.in_routine:
            raise ln.err("'choose' is not allowed in a background routine")
        n = self.visible_bound(options)
        if n > MAX_OPTIONS:
            raise ln.err(f"up to {n} options visible at once, at most {MAX_OPTIONS}")
        self.max_visible = max(self.max_visible, n)
        l_top, l_end = self.label("choose"), self.label("chosen")
        targets = [self.label("opt") for _ in options]
        self.b.mark(l_top)
        self.op("CHOICES")
        for opt, target in zip(options, targets):
            # The list shows the first line, the selected option appears in full.
            try:
                lines = wrap(self.src.line(opt["text"]), OPTION_COLS, self.src.widths())
            except TextError as e:
                raise opt["line"].err(str(e))
            # Once it is selected, the list below shrinks down to one line.
            if len(lines) > SCREEN_ROWS - 1:
                raise opt["line"].err(f"option {opt['text']} ({self.src.name}) needs {len(lines)} lines; "
                                      f"together with the option list that does not fit on the display")
            skip = self.label("optskip")
            if opt["cond"]:
                self.jump_unless(opt["line"], opt["cond"], skip)
            self.text_max = max(self.text_max, self.shown_len("\n".join(lines)))
            self.op("OPTION", self.string("\n".join(lines)), target)
            self.b.mark(skip)
        self.op("ASK")
        # If all options are hidden, the engine continues here.
        self.op("JMP", l_end)
        self.choose_end = getattr(self, "choose_end", [])
        for opt, target in zip(options, targets):
            self.b.mark(target)
            self.choose_end.append(l_end)
            self.stmts(opt["body"])
            self.choose_end.pop()
            self.op("JMP", l_top)
        self.b.mark(l_end)

    def st_cmd(self, ln, toks):
        kw, *a = toks

        def need(n, syntax):
            if len(a) != n:
                raise ln.err(f"Syntax: {syntax}")

        if self.in_routine and kw in ("say", "room", "card", "pickup", "lose", "start", "stop"):
            raise ln.err(f"'{kw}' is not allowed in a background routine")
        if kw == "say":
            need(2, "say <actor|narrator> <text>  (reference, e.g. r38.s203#1 or r38.s203#1:2)")
            actor = self.actor(ln, a[0])
            for bubble in self.bubbles(ln, a[1]):
                self.text_max = max(self.text_max, self.shown_len(bubble))
                self.op("SAY", actor, self.string(bubble))
        elif kw == "costume":
            need(2, "costume <actor> <like-actor>  (back: costume <actor> <actor>)")
            actor, look = self.actor(ln, a[0]), self.actor(ln, a[1])
            if NONE8 in (actor, look):
                raise ln.err("costume needs two actors")
            self.op("COSTUME", actor, look)
        elif kw == "pan":
            need(1, "pan <x>  (room coordinate, centre of the screen)")
            try:
                x = int(a[0], 0)
            except ValueError:
                raise ln.err("pan: x must be a number")
            if not 0 <= x < 0x8000:
                raise ln.err("pan: 0 ≤ x < 32768")
            self.op("PAN", x)
        elif kw == "flash":
            need(1, "flash <frames>  (60 = 1 s)")
            n = int(a[0])
            if not 0 < n < 256:
                raise ln.err("flash: 1…255 frames")
            self.op("FLASH", n)
        elif kw == "call":
            need(1, "call <sub>")
            if self.in_routine:
                raise ln.err("call is not allowed in a background routine (play starts a cutscene)")
            if a[0] not in self.g.subs:
                raise ln.err(f"unknown subroutine '{a[0]}'")
            self.op("CALL", self.L(f"sub_{a[0]}"))
        elif kw == "play":
            need(1, "play <cutscene>")
            if not self.in_routine:
                raise ln.err("play only in a background routine (in the foreground write the commands directly)")
            if a[0] not in self.g.cutscenes:
                raise ln.err(f"unknown cutscene '{a[0]}'")
            self.op("PLAY", self.L(f"cutscene_{a[0]}"))
        elif kw == "let" and len(a) == 4 and a[1] == "random":
            try:
                lo, hi = int(a[2], 0), int(a[3], 0)
            except ValueError:
                raise ln.err("let <variable> random <min> <max>")
            if not 0 <= lo <= hi < 256:
                raise ln.err("let … random: 0 ≤ min ≤ max ≤ 255")
            self.op("LETR", self.var(ln, a[0]), lo, hi)
        elif kw in ("let", "add"):
            need(2, f"{kw} <variable> <number>")
            try:
                value = int(a[1], 0)
            except ValueError:
                raise ln.err(f"{kw}: number expected")
            if not (0 <= value < 256 if kw == "let" else -128 <= value < 128):
                raise ln.err("let: 0…255, add: -128…127")
            self.op(kw.upper(), self.var(ln, a[0]), value & 0xFF)
        elif kw == "setstring":
            need(2, "setstring <string> <text>")
            sid = self.string_var(ln, a[0])
            self.op("SETSTR", self.g.strings[sid]["slot"], self.text_line(ln, a[1]))
        elif kw == "setchar":
            need(3, "setchar <string> <position> <charcode>")
            sid = self.string_var(ln, a[0])
            try:
                pos = int(a[1], 0)
            except ValueError:
                raise ln.err("setchar: position must be a number")
            if not 0 <= pos < self.string_size - 1:
                raise ln.err(f"setchar: position {pos} lies outside the string")
            if re.fullmatch(r"\d+|0x[0-9a-fA-F]+", a[2]):
                ch = int(a[2], 0)
                if not 0 < ch < 256:
                    raise ln.err("setchar: character code 1…255")
                self.op("SETCHAR", self.g.strings[sid]["slot"], pos, ch)
            else:
                self.op("SETCHARV", self.g.strings[sid]["slot"], pos, self.var(ln, a[2]))
        elif kw in ("remove", "halt"):
            need(1, f"{kw} <actor>")
            self.op(kw.upper(), self.actor(ln, a[0]))
        elif kw in ("walk", "put"):
            need(3, f"{kw} <actor> x y")
            try:
                x, y = int(a[1], 0), int(a[2], 0)
            except ValueError:
                raise ln.err(f"{kw}: coordinates must be numbers")
            self.op(kw.upper(), self.actor(ln, a[0]), x, y)
        elif kw == "face":
            need(2, "face <actor> left|right|front")
            if a[1] not in DIRS:
                raise ln.err("direction: left|right|front")
            self.op("FACE", self.actor(ln, a[0]), DIRS[a[1]])
        elif kw in ("set", "clear"):
            need(1, f"{kw} <flag>")
            self.op(kw.upper(), self.flag(a[0]))
        elif kw in ("pickup", "lose", "hide", "show"):
            need(1, f"{kw} <object>")
            self.op(kw.upper(), self.obj(ln, a[0]))
        elif kw == "state":
            need(2, "state <object> <n>")
            self.op("STATE", self.obj(ln, a[0]), int(a[1]))
        elif kw == "music":
            need(1, "music <id>|stop")
            if a[0] == "stop":
                self.op("MUSIC", NONE8)
            elif a[0] in self.g.music:
                self.op("MUSIC", self.g.music[a[0]]["index"])
            else:
                raise ln.err(f"unknown music '{a[0]}'")
        elif kw == "wait":
            if a[:1] == ["random"]:
                need(3, "wait random <min> <max>  (frames, 60 = 1 s)")
                lo, hi = int(a[1]), int(a[2])
                if not 0 < lo <= hi < 0x10000:
                    raise ln.err("wait random: 0 < min ≤ max < 65536")
                self.op("WAITR", lo, hi)
            else:
                need(1, "wait <frames> | wait random <min> <max>")
                n = int(a[0])
                if n < 256:
                    self.op("WAIT", n)
                else:
                    self.op("WAITR", n, n)
        elif kw == "start":
            need(1, "start <routine>")
            if a[0] not in self.g.routines:
                raise ln.err(f"unknown routine '{a[0]}'")
            self.op("START", self.L(f"routine_{a[0]}"))
        elif kw == "stop":
            need(1, "stop <routine>")
            if a[0] not in self.g.routines:
                raise ln.err(f"unknown routine '{a[0]}'")
            self.op("STOP", self.L(f"routine_{a[0]}"))
        elif kw == "room":
            # room <room> [at x y] [face dir]
            if not a or a[0] not in self.g.rooms:
                raise ln.err(f"unknown room '{a[0] if a else ''}' (Syntax: room <room> [at x y] [face dir])")
            if self.in_entry:
                raise ln.err("entry scripts must not change the room")
            rest = a[1:]
            x, y, face = 0xFFFF, 0, DIRS["front"]
            if rest[:1] == ["at"]:
                if len(rest) < 3:
                    raise ln.err("room … at x y")
                try:
                    x, y = int(rest[1], 0), int(rest[2], 0)
                except ValueError:
                    raise ln.err("room … at: coordinates must be numbers")
                rest = rest[3:]
            if rest[:1] == ["face"]:
                if len(rest) < 2 or rest[1] not in DIRS:
                    raise ln.err("room … face left|right|front")
                face = DIRS[rest[1]]
                rest = rest[2:]
            if rest:
                raise ln.err(f"room: unexpected {rest}")
            self.op("ROOM", self.g.rooms[a[0]]["index"], x, y, face)
        elif kw == "card":
            need(1, "card <id>")
            if a[0] not in self.g.cards:
                raise ln.err(f"unknown card '{a[0]}'")
            c = self.g.cards[a[0]]
            music = NONE8
            if c["music"]:
                if c["music"] not in self.g.music:
                    raise c["line"].err(f"unknown music '{c['music']}'")
                music = self.g.music[c["music"]]["index"]
            self.op("CARD", *self.card_image(a[0]), music)
        elif kw == "done":
            need(0, "done")
            if not getattr(self, "choose_end", None):
                raise ln.err("'done' only inside a choose option")
            self.op("JMP", self.choose_end[-1])
        else:
            raise ln.err(f"unknown command '{kw}'")

    # ---- Overall layout ----
    #
    # game.bin:  LangDir + LangEntry per language
    #            shared: characters, walk paths, placements, music
    #            per language: GameHeader, verbs, objects, rooms, scripts, texts
    #            shared: images, language names

    def compile(self):
        g, b = self.g, self.b
        self.check()
        if not g.languages:
            raise CompileError("no language version given (advc.py --original <copy> …)")
        if len(g.objects) > 254 or len(g.actors) > 254 or len(g.rooms) > 254:
            raise CompileError("at most 254 objects/actors/rooms")

        b.mark("langdir")
        b.raw(bytes(record_size("LangDir") + len(g.languages) * record_size("LangEntry")))

        for name in g.numbers:
            self.var(None, name)
        for src in g.languages:
            src.strings = self.string_widths(src)
            src.numbers = {orig: g.vars[name] for name, orig in g.numbers.items()}
        self.string_size = 1 + max([w for src in g.languages for _, w in src.strings.values()], default=0)
        if self.string_size > 64:
            raise CompileError("string variables at most 63 characters")

        self.call_stack = self.call_depth()
        self.check_greyscale()
        self.compile_shared()
        for src in g.languages:
            self.src = src
            self.strings = {}
            self.compile_language()

        # Images
        cursor = self.image(g.cursor, None, mask=True) if g.cursor else None
        title = self.image(g.title, None) if g.title else None
        self.cursor_label = cursor["label"] if cursor else NONE24
        self.title_label = title["label"] if title else NONE24
        self.title_grey_label = self.title_grey()
        for img in self.images.values():
            b.mark(img["label"])
            b.raw(img["data"])
        for src in g.languages:
            b.mark(f"langname:{src.code}")
            b.raw(src.name.encode("cp437") + b"\0")

        # Headers only now (cursor and title are stored by now) into their
        # reserved slots.
        b.resolve()
        for src in g.languages:
            self.src = src
            head = self.header_record()
            head.resolve()
            at = b.labels[self.L("header")]
            b.data[at:at + len(head.data)] = head.data

        # The area of the language directory is still zeroed here; so the
        # build ID depends only on the content.
        self.build_id = int.from_bytes(hashlib.sha1(bytes(b.data)).digest()[:2], "little")
        head = Blob()
        head.labels = b.labels
        head.record("LangDir", magic=0x464D, buildId=self.build_id, count=len(g.languages))
        for src in g.languages:
            head.record("LangEntry", name=f"langname:{src.code}", header=f"{src.code}:header")
        head.resolve()
        b.data[0:len(head.data)] = head.data
        if len(b.data) >= 1 << 24:
            raise CompileError("data larger than 16 MB")
        return bytes(b.data)

    def compile_shared(self):
        """Parts without text: characters, walk paths and placements, music."""
        g, b = self.g, self.b
        b.mark("actors")
        for aid, a in g.actors.items():
            img = self.image(a["sprite"], a["line"], mask=True)
            first, count = a["walk"]
            for f in (a["stand"], a["talk"], a["front"], a["fronttalk"], first, first + count - 1):
                if not 0 <= f < img["frames"]:
                    raise a["line"].err(f"frame {f} does not exist (sprite has {img['frames']})")
            obj = self.obj(a["line"], a["object"]) if a.get("object") else NONE8
            b.record("ActorRec", sprite=img["label"], grey=self.figure_grey(a.get("grey"), a["line"]),
                     stand=a["stand"], walkFirst=first, walkCount=count, talk=a["talk"],
                     front=a["front"], frontTalk=a["fronttalk"], object=obj,
                     depth=f"depth_{aid}" if a.get("levels") else NONE24)
        for aid, a in g.actors.items():
            if not a.get("levels"):
                continue
            # Descending: a level applies as soon as the size is below the midpoint between
            # its fraction and the next larger one (u8 threshold, u24 sprite,
            # u24 sprite in greyscale).
            b.mark(f"depth_{aid}")
            b.put("u8", len(a["levels"]))
            prev = 1.0
            for f, sprite, grey in a["levels"]:
                img = self.image(sprite, a["line"], mask=True)
                b.put("u8", round((f + prev) / 2 * 255))
                b.put("u24", img["label"])
                b.put("u24", self.figure_grey(grey, a["line"]))
                prev = f

        for rid, r in g.rooms.items():
            bg = self.image(r["bg"], r["line"])
            if not 64 <= bg["h"] <= 255 or bg["w"] < 128:
                raise r["line"].err("room image must be 64–255 px high and at least 128 px wide")
            r["width"] = bg["w"]
            r["height"] = bg["h"]
            r["bg_label"] = bg["label"]
            r["grey_label"] = self.room_grey(rid, r)
            if not r["boxes"] and not r.get("blank"):
                raise r["line"].err("room without walk boxes (walkbox / walkboxes original)")
            if len(r["boxes"]) > 254:
                raise r["line"].err("at most 254 walk boxes per room (NONE8 marks “no path”)")
            b.mark(f"boxes_{rid}")
            for corners, ln, (top, bottom) in r["boxes"]:
                pts = [(int(round(x)), int(round(y))) for x, y in corners]
                # Walk paths may extend beyond the image edge: stairs going
                # down, exits sideways out of the image (as in the original).
                if not all(0 <= x < 0x8000 and 0 <= y < 256 for x, y in pts):
                    raise ln.err(f"walk box {pts} out of range")
                (ulx, uly), (urx, ury), (lrx, lry), (llx, lly) = pts
                b.record("BoxRec", ulx=ulx, uly=uly, urx=urx, ury=ury,
                         lrx=lrx, lry=lry, llx=llx, lly=lly, scaleTop=top, scaleBottom=bottom)
            b.mark(f"matrix_{rid}")
            b.raw(bytes(v for row in box_matrix([c for c, _, _ in r["boxes"]]) for v in row))
            b.mark(f"places_{rid}")
            for p in r["places"]:
                ln = p["line"]
                oi = NONE8 if p["object"] == "decor" else self.obj(ln, p["object"])
                if p["image"]:
                    img = self.image(p["image"], ln, mask=True)
                    w, h = p["w"] or img["w"], p["h"] or img["h"]
                    if img["frames"] % p["frames"]:
                        raise ln.err(f"frames {p['frames']} does not divide the {img['frames']} images")
                    image = img["label"]
                    grey = self.place_grey(rid, r, p) if p["grey_from"] else self.figure_grey(p["grey_sprite"], ln)
                else:
                    if p["w"] is None:
                        raise ln.err("hotspot without an image needs w h")
                    w, h, image, grey = p["w"], p["h"], NONE24, NONE24
                b.record("PlaceRec", object=oi, x=p["x"], y=p["y"], w=w, h=h,
                         walkX=p["walk"][0], walkY=p["walk"][1], face=p["face"],
                         image=image, grey=grey, frames=p["frames"], speed=p["speed"],
                         backdrop=p["backdrop"])

        b.mark("music")
        for mid in g.music:
            b.put("u24", f"track_{mid}")
        for mid, m in g.music.items():
            b.mark(f"track_{mid}")
            b.record("TrackRec", count=len(m["notes"]), loop=int(m["loop"]))
            for ocr, ms in m["notes"]:
                b.record("NoteRec", ocr=ocr, ms=ms)

    def compile_language(self):
        """Everything with text for the language self.src: verbs, objects, rooms
        (because of the entry scripts), scripts, texts. The header follows later."""
        g, b, L = self.g, self.b, self.L
        b.mark(L("header"))
        b.raw(bytes(record_size("GameHeader")))

        b.mark(L("verbs"))
        for vid, v in g.verbs.items():
            fallback = L(f"default_{vid}") if vid in g.defaults else NONE24
            b.record("VerbRec", name=self.text_line(None, v["name"]),
                     prep=self.text_line(None, v["prep"]) if v["prep"] else NONE24, fallback=fallback)

        b.mark(L("objects"))
        for oid, o in g.objects.items():
            b.record("ObjectRec", name=self.text_line(o["line"], o["name"]), verbs=L(f"objverbs_{oid}"))
        for oid, o in g.objects.items():
            b.mark(L(f"objverbs_{oid}"))
            seen = set()
            for i, h in enumerate(o["verbs"]):
                if h["verb"] != "other" and h["verb"] not in g.verbs:
                    raise h["line"].err(f"unknown verb '{h['verb']}'")
                if h["verb"] == "other" and h["other"]:
                    raise h["line"].err("'on other' applies to all remaining verbs, without a second object")
                other = self.obj(h["line"], h["other"]) if h["other"] else NONE8
                key = (h["verb"], other)
                if key in seen:
                    raise h["line"].err("handler given twice")
                seen.add(key)
                verb = VERB_ANY if h["verb"] == "other" else g.verbs[h["verb"]]["index"]
                b.record("VerbEntry", verb=verb, other=other,
                         script=L(f"objscript_{oid}_{i}"))
            b.put("u8", NONE8)

        b.mark(L("rooms"))
        for rid, r in g.rooms.items():
            b.record("RoomRec", background=r["bg_label"], grey=r["grey_label"],
                     width=r["width"], height=r["height"],
                     boxCount=len(r["boxes"]), boxes=f"boxes_{rid}", matrix=f"matrix_{rid}",
                     placeCount=len(r["places"]), places=f"places_{rid}",
                     entry=L(f"entry_{rid}"))

        for vid, body in g.defaults.items():
            self.script(body, L(f"default_{vid}"))
        for oid, o in g.objects.items():
            for i, h in enumerate(o["verbs"]):
                self.script(h["body"], L(f"objscript_{oid}_{i}"))
        self.in_entry = True
        for rid, r in g.rooms.items():
            self.script(r["entry"], L(f"entry_{rid}"))
        self.in_entry = False
        self.in_routine = True
        for rid, (ln, body) in g.routines.items():
            self.script(body, L(f"routine_{rid}"))
        self.in_routine = False
        for cid, (ln, body) in g.cutscenes.items():
            self.script(body, L(f"cutscene_{cid}"))
        for sid, (ln, body) in g.subs.items():
            self.script(body, L(f"sub_{sid}"))
        start = g.start_room
        if isinstance(start, tuple):  # short form: start <room>
            start_room, start_ln = start
            if start_room not in g.rooms:
                raise start_ln.err(f"unknown room '{start_room}'")
            b.mark(L("start"))
            self.op("ROOM", g.rooms[start_room]["index"], 0xFFFF, 0, 0)
            self.op("END")
        else:
            self.script(start, L("start"))

        ui = UI_TEXT.get(self.src.code, UI_TEXT["en"])
        self.lang_ui[self.src.code] = {key: self.string(text) for key, text in ui.items()}

        # texts last: they are created during code generation
        for text, label in self.strings.items():
            b.mark(label)
            b.raw(self.encode_text(text) + b"\0")

    def header_record(self):
        """GameHeader of the language self.src (labels via the main blob)."""
        g, L = self.g, self.L
        title_music = NONE8
        if g.title_music:
            name, ln = g.title_music
            if name not in g.music:
                raise ln.err(f"unknown music '{name}'")
            title_music = g.music[name]["index"]
        inventory_verb = NONE8
        if g.inventory_verb:
            name, ln = g.inventory_verb
            if name not in g.verbs:
                raise ln.err(f"unknown verb '{name}'")
            inventory_verb = g.verbs[name]["index"]
        ui = self.lang_ui[self.src.code]
        head = Blob()
        head.labels = self.b.labels
        head.record("GameHeader",
                    verbCount=len(g.verbs), verbs=L("verbs"),
                    objectCount=len(g.objects), objects=L("objects"),
                    actorCount=len(g.actors), actors="actors",
                    roomCount=len(g.rooms), rooms=L("rooms"),
                    musicCount=len(g.music), music="music",
                    startScript=L("start"), cursor=self.cursor_label, title=self.title_label,
                    titleGrey=self.title_grey_label,
                    titleMusic=title_music, inventoryVerb=inventory_verb,
                    uiSoundOn=ui["sound_on"], uiSoundOff=ui["sound_off"], uiEmpty=ui["empty"],
                    uiGreyOn=ui["grey_on"], uiGreyOff=ui["grey_off"])
        return head

    def check(self):
        g = self.g
        for req, what in ((g.verbs, "verb"), (g.actors, "actor"), (g.rooms, "room")):
            if not req:
                raise CompileError(f"at least one '{what}' required")
        if g.start_room is None:
            raise CompileError("'start <room>' missing")
        if "walk" not in g.verbs or g.verbs["walk"]["index"] != 0:
            raise CompileError("the first verb must be 'walk' (the engine's default verb)")

    # ---- Header for the engine ----

    def header(self):
        g = self.g
        out = [
            "// Generated by tools/advc.py – do not edit by hand.",
            "#pragma once",
            "#include <stdint.h>",
            "",
            f"constexpr uint16_t GAME_MAGIC = 0x464D;",
            f"constexpr uint16_t GAME_BUILD_ID = 0x{self.build_id:04X};",
            f"constexpr uint8_t VERB_COUNT = {len(g.verbs)};",
            f"constexpr uint8_t OBJECT_COUNT = {len(g.objects)};",
            f"constexpr uint8_t ACTOR_COUNT = {len(g.actors)};",
            f"constexpr uint8_t FLAG_BYTES = {max(1, (len(g.flags) + 7) // 8)};",
            f"constexpr uint8_t VAR_COUNT = {max(1, len(g.vars))};",
            f"constexpr uint8_t MAX_WALKBOXES = {max(len(r['boxes']) for r in g.rooms.values())};",
            f"constexpr uint8_t MAX_INVENTORY = {MAX_INVENTORY};",
            f"constexpr uint8_t MAX_OPTIONS = {self.max_visible};  // options visible at once",
            f"constexpr uint8_t CHOICE_ROWS = {CHOICE_ROWS};",
            f"constexpr uint8_t TEXT_COLS = {TEXT_COLS};",
            f"constexpr uint8_t TEXT_ROWS = {TEXT_ROWS};",
            f"constexpr uint8_t OPTION_COLS = {OPTION_COLS};",
            f"constexpr uint8_t STRING_SLOTS = {max(1, len(g.strings))};",
            f"constexpr uint8_t CALL_DEPTH = {self.call_stack};  // return addresses (call, entry scripts)",
            f"constexpr uint8_t STRING_SIZE = {self.string_size};",
            f"constexpr uint8_t TEXT_BUFFER = {self.text_buffer()};  // longest speech bubble/option + NUL",
            "",
            "constexpr uint8_t COND_FLAG = 0x00, COND_HAS = 0x01, COND_OPEN = 0x02, COND_HOVER = 0x03, COND_NOT = 0x80;",
            "constexpr uint8_t POS_Y = 0x01, POS_GT = 0x02;",
            "constexpr uint8_t VAR_EQ = 0x00, VAR_LT = 0x01, VAR_GT = 0x02;",
            "constexpr uint8_t VERB_ANY = 0xFE;  // VerbEntry.verb: „on other“",
            "constexpr uint16_t WALK_DIRECT = 0xFFFF;  // PlaceRec.walkX: verb script starts without walking there",
            "",
            "enum Op : uint8_t {",
        ]
        out += [f"  OP_{name} = {i}," for i, (name, _) in enumerate(OPCODES)]
        out += ["};", ""]
        out.append("// command lengths in bytes incl. opcode (CHOOSE: without the option records)")
        lens = [1 + sum(SIZES[t] for t in ops) for _, ops in OPCODES]
        out.append(f"constexpr uint8_t OP_LENGTH[] = {{{', '.join(map(str, lens))}}};")
        out.append(f"constexpr uint8_t OP_MAX_LENGTH = {max(lens)};")
        out.append("")
        for kind, table in (("VERB", g.verbs), ("OBJ", g.objects), ("ACTOR", g.actors), ("ROOM", g.rooms), ("MUSIC", g.music)):
            for ident, v in table.items():
                out.append(f"constexpr uint8_t {kind}_{ident.upper()} = {v['index']};")
            out.append("")
        for name, spec in RECORDS.items():
            out.append(f"struct {name} {{")
            out += [f"  {CTYPES[t]} {f};" for f, t in spec]
            out.append("};")
            out.append(f'static_assert(sizeof({name}) == {record_size(name)}, "{name}: layout does not match advc.py");')
            out.append("")
        return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Pocket Adventure FX: scene description → FX data")
    ap.add_argument("source", type=Path, help="scene description (.adv)")
    ap.add_argument("--bin", type=Path, required=True, help="output: FX data block")
    ap.add_argument("--header", type=Path, required=True, help="output: gamedata.h")
    ap.add_argument("--original", type=Path, action="append", default=[],
                    help="directory of a copy of the original game (DISK01.LEC …); repeat for several languages. "
                         "Graphics, walk boxes and music come from the first, the texts from each.")
    ap.add_argument("--preview", type=Path, help="write images generated from the original data here as PNG")
    args = ap.parse_args()
    try:
        game = Game(args.source.resolve().parent)
        if args.original:
            game.original_dir = args.original[0]
        for d in args.original:
            try:
                src = TextSource(d)
            except (TextError, OSError) as e:
                raise CompileError(str(e))
            if any(other.code == src.code for other in game.languages):
                raise CompileError(f"{d}: language {src.name} is already given")
            game.languages.append(src)
        Parser(game, tokenize(args.source.read_text(encoding="utf-8"))).parse()
        comp = Compiler(game)
        data = comp.compile()
    except CompileError as e:
        print(f"{args.source}: {e}", file=sys.stderr)
        return 1
    args.bin.write_bytes(data)
    args.header.write_text(comp.header())
    if args.preview:
        args.preview.mkdir(parents=True, exist_ok=True)
        for img in comp.images.values():
            if isinstance(img["source"], tuple):
                key = img["source"][1]
                preview = comp.grey_previews[key] if key in comp.grey_previews else img["source"][2]
                preview.save(args.preview / f"{key}.png")
    music = sum(len(m["notes"]) for m in game.music.values())
    langs = ", ".join(src.name for src in game.languages)
    print(f"advc: {len(data)} bytes, languages: {langs}, {len(game.rooms)} room(s), {len(game.objects)} objects, "
          f"{len(game.flags)} flags, {music} notes, build 0x{comp.build_id:04X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
