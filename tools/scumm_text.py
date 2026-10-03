#!/usr/bin/env python3
"""Texts from the SCUMM v4 scripts of a copy of the game (Monkey Island 1, VGA).

Every string in the bytecode gets an identifier that refers to the same line
in all language versions – the translations replace only the texts, not the
scripts:

  s<n>#k           k-th text in global script n
  r<room>.s<n>#k   k-th text in room script n
  r<room>.en#k     entry code of the room (ex: exit)
  o<object>#k      code of the object (all verbs, in bytecode order)
  o<object>.name   object name

Counting starts at 1, in bytecode order. This requires skipping every command
including its parameters exactly; the decoder follows the one from
descumm (scummvm-tools, engines/scumm/descumm.cpp, next_line_V345) for
version 4. The tests compare both across all scripts of both versions.

Strings consist of text in code page 437 and control sequences
(0xFF/0xFE, code): 1 line break, 2 keep text, 3 wait (new
speech bubble), 4–7 insert variable, 9 animation, 10 speech,
12 colour, 13 unknown, 14 font.

Run as a script: lists the texts of a copy.
"""
import argparse
import re
import struct
import sys
from pathlib import Path

from scumm_v4 import XOR_KEY, ScummError, blocks, find

# Control codes a text may contain, and what they turn into.
NEWLINE, KEEP, WAIT = 1, 2, 3
STRING, SOFT_NEWLINE = 7, 8
# Placeholder in the result of plain(): marker, then chr(SLOT_BASE + slot).
# The slot is a character from the Unicode private use area so that wrapping and
# whitespace handling never mistake it for a control or space character;
# only when stored for the engine does it become a byte (slot + 1).
STRING_VAR = "\x01"  # string variable (code 7)
INT_VAR = "\x03"     # number (code 4)
SLOT_BASE = 0xE000
INT = 4
VAR_CODES = {4: "int", 5: "verb", 6: "name", 7: "string"}


class Text:
    """A string from the bytecode: list of str and (code, value)."""

    def __init__(self, ident, kind, parts, offset):
        self.ident = ident
        self.kind = kind          # print, printEgo, verb, actorName, objectName, setObjectName, codeString, file
        self.parts = parts
        self.offset = offset      # position in the block (for mapping to verbs)
        self.verb = None          # for object code: verb number whose code contains the text

    def plain(self, strings=None, numbers=None):
        """Text without control codes; line break → '\\n', wait → '\\f'.

        Code 8 (line break in long dialogue options) also becomes '\\n'.
        Code 7 outputs a string variable; strings maps its number in the
        original to an engine slot, and the text then contains
        STRING_VAR + chr(SLOT_BASE + slot). Code 4 (number) likewise with numbers
        and INT_VAR."""
        out = []
        for p in self.parts:
            if isinstance(p, str):
                out.append(p)
            elif p[0] in (NEWLINE, SOFT_NEWLINE):
                out.append("\n")
            elif p[0] == WAIT:
                out.append("\f")
            elif p[0] == KEEP:
                pass
            elif p[0] == STRING and strings and p[1] in strings:
                out.append(STRING_VAR + chr(SLOT_BASE + strings[p[1]]))
            elif p[0] == INT and numbers and p[1] in numbers:
                out.append(INT_VAR + chr(SLOT_BASE + numbers[p[1]]))
            else:
                raise ScummError(f"{self.ident}: text contains control code {p[0]} ({self.render()!r})")
        return "".join(out)

    def render(self):
        """Rendering like descumm (get_string) – for tests and listings."""
        out = []
        for p in self.parts:
            if isinstance(p, str):
                out.append('"' + p.replace("\\", "\\\\").replace('"', '\\"') + '"')
            else:
                out.append(f"<{p[0]}:{p[1]}>" if p[1] is not None else f"<{p[0]}>")
        return " + ".join(out)


class Decoder:
    """Runs linearly through a v4 script block and collects the texts."""

    def __init__(self, code, start):
        self.code = code
        self.pos = start
        self.texts = []           # (offset, kind, parts)

    # --- Basic types (descumm: get_byte, get_word, get_var, get_list) ----------
    def byte(self):
        b = self.code[self.pos]
        self.pos += 1
        return b

    def word(self):
        w, = struct.unpack_from("<H", self.code, self.pos)
        self.pos += 2
        return w

    def var(self):
        i = self.word()
        if i & 0x2000:
            self.word()

    def var_or_word(self, is_var):
        self.var() if is_var else self.word()

    def var_or_byte(self, is_var):
        self.var() if is_var else self.byte()

    def lst(self):
        for _ in range(17):
            i = self.byte()
            if i == 0xFF:
                return
            self.var_or_word(i & 0x80)
        raise ScummError(f"argument list too long at {self.pos:#x}")

    def string(self, kind):
        at = self.pos
        parts, run = [], bytearray()
        while True:
            c = self.byte()
            if c == 0:
                break
            if c in (0xFF, 0xFE):
                if run:
                    parts.append(run.decode("cp437"))
                    run = bytearray()
                code = self.byte()
                value = None
                if code in (NEWLINE, KEEP, WAIT):
                    pass
                elif code in VAR_CODES:
                    value = self.word()
                    if value & 0x2000:
                        self.word()
                elif code == 10:
                    # Speech: 4 words, each separated by the bytes FF 0A
                    value = self.word()
                    self.pos += 2
                    self.word()
                    self.pos += 2
                    self.word()
                    self.pos += 2
                    self.word()
                else:
                    value = self.word()
                parts.append((code, value))
            else:
                run.append(c)
        if run:
            parts.append(run.decode("cp437"))
        self.texts.append((at, kind, parts))

    def args(self, *spec):
        """Arguments by descumm abbreviations: B, W, V, L, A (text)."""
        for s in spec:
            {"B": self.byte, "W": self.word, "V": self.var, "L": self.lst}[s]()

    def a(self, opcode, bit, small):
        """One argument: a variable if the bit is set in the opcode, else byte/word."""
        if opcode & bit:
            self.var()
        else:
            self.byte() if small == "B" else self.word()

    # --- Commands ---------------------------------------------------------------
    def run(self):
        while self.pos < len(self.code):
            self.step()
        return self.texts

    def step(self):
        op = self.byte()
        spec = OPCODES.get(op)
        if spec is None:
            raise ScummError(f"unknown opcode {op:#04x} at {self.pos - 1:#x}")
        if callable(spec):
            spec(self, op)
            return
        bits = iter((0x80, 0x40, 0x20))
        for s in spec:
            if s == "S":                                              # target variable
                self.var()
            elif s in "BW":                                           # variable or constant
                self.a(op, next(bits), s)
            elif s == "b":
                self.byte()
            elif s in "wJ":                                           # word, jump target
                self.word()
            elif s == "v":
                self.var()
            elif s == "L":
                self.lst()

    def do_sentence(self, op):
        if not op & 0x80 and self.code[self.pos] == 0xFE:
            self.pos += 1                                             # doSentence(STOP)
        else:
            self.a(op, 0x80, "B"); self.a(op, 0x40, "W"); self.a(op, 0x20, "W")

    def load_room_with_ego(self, op):
        self.a(op, 0x80, "W"); self.a(op, 0x40, "B"); self.word(); self.word()

    def set_var_range(self, op):
        self.var()
        for _ in range(self.byte()):
            self.word() if op & 0x80 else self.byte()

    def draw_box(self, op):
        self.a(op, 0x80, "W"); self.a(op, 0x40, "W")
        sub = self.byte()
        self.a(sub, 0x80, "W"); self.a(sub, 0x40, "W"); self.a(sub, 0x20, "B")

    def set_object_name(self, op):
        self.a(op, 0x80, "W")
        self.string("setObjectName")

    def old_room_effect(self, op):
        self.byte()
        self.a(op, 0x80, "W")

    def save_verbs(self, op):
        sub = self.byte()
        self.a(sub, 0x80, "B"); self.a(sub, 0x40, "B"); self.a(sub, 0x20, "B")

    def wait(self, op):
        sub = self.byte()
        if sub in (0x01, 0x81):
            self.a(sub, 0x80, "B")

    def pseudo_room(self, op):
        self.byte()
        while self.byte():
            pass

    def delay(self, op):
        self.pos += 3

    def resource(self, op=None):
        sub = self.byte()
        code = sub & 0x3F
        if code == 0x11:
            return
        self.a(sub, 0x80, "B")
        if code == 0x14:                                              # loadFlObject
            self.a(sub, 0x40, "W")
        elif code in (0x23, 0x25):
            self.a(sub, 0x40, "B")
        elif code == 0x24:
            self.a(sub, 0x40, "B"); self.byte()
        elif not 1 <= code <= 0x13:
            raise ScummError(f"Resource: unknown subcode {code:#x}")

    def actor_ops(self, op):
        convert = (1, 0, 0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 20)
        self.a(op, 0x80, "B")
        while True:
            sub = self.byte()
            if sub == 0xFF:
                return
            sub = (sub & 0xE0) | convert[(sub & 0x1F) - 1]
            code = sub & 0x1F
            if code in (0x00, 0x01, 0x03, 0x04, 0x06, 0x0C, 0x0E, 0x10, 0x11, 0x13, 0x16, 0x17):
                self.a(sub, 0x80, "B")
            elif code in (0x02, 0x05, 0x0B):
                self.a(sub, 0x80, "B"); self.a(sub, 0x40, "B")
            elif code == 0x07:
                self.a(sub, 0x80, "B"); self.a(sub, 0x40, "B"); self.a(sub, 0x20, "B")
            elif code == 0x09:
                self.a(sub, 0x80, "W")
            elif code == 0x0D:
                self.string("actorName")
            elif code not in (0x08, 0x0A, 0x12, 0x14, 0x15):
                raise ScummError(f"ActorOps: unknown subcode {sub:#x}")

    def print_ego(self, op):
        if op != 0xD8:
            self.a(op, 0x80, "B")
        kind = "printEgo" if op == 0xD8 else "print"
        while True:
            sub = self.byte()
            if sub == 0xFF:
                return
            code = sub & 0x1F
            if code in (0x0, 0x3, 0x8):
                self.a(sub, 0x80, "W"); self.a(sub, 0x40, "W")
            elif code == 0x1:
                self.a(sub, 0x80, "B")
            elif code == 0x2:
                self.a(sub, 0x80, "W")
            elif code in (0x4, 0x6, 0x7):
                pass
            elif code == 0xF:
                self.string(kind)
                return                                                # text ends the list
            else:
                raise ScummError(f"print: unknown subcode {sub:#x}")

    def string_ops(self, op=None):
        sub = self.byte()
        code = sub & 0x1F
        if code == 0x01:
            self.a(sub, 0x80, "B")
            self.string("codeString")
        elif code in (0x02, 0x05):
            self.a(sub, 0x80, "B"); self.a(sub, 0x40, "B")
        elif code == 0x03:
            self.a(sub, 0x80, "B"); self.a(sub, 0x40, "B"); self.a(sub, 0x20, "B")
        elif code == 0x04:
            self.var(); self.a(sub, 0x80, "B"); self.a(sub, 0x40, "B")
        else:
            raise ScummError(f"Stringops: unknown subcode {sub:#x}")

    def cursor(self, op=None):
        sub = self.byte()
        code = sub & 0x1F
        if code in (0x0C, 0x0D):
            self.a(sub, 0x80, "B")
        elif code == 0x0A:
            self.a(sub, 0x80, "B"); self.a(sub, 0x40, "B")
        elif code == 0x0B:
            self.a(sub, 0x80, "B"); self.a(sub, 0x40, "B"); self.a(sub, 0x20, "B")
        elif code == 0x0E:
            self.lst()
        elif not 1 <= code <= 8:
            raise ScummError(f"Cursor: unknown subcode {sub:#x}")

    def matrix_ops(self, op=None):
        sub = self.byte()
        code = sub & 0x1F
        if code in (1, 2, 3):
            self.a(sub, 0x80, "B"); self.a(sub, 0x40, "B")
        elif code != 4:
            raise ScummError(f"Boxops: unknown subcode {sub:#x}")

    def room_ops(self, op=None):
        sub = self.byte()
        code = sub & 0x1F
        if code in (1, 2, 3, 4):
            self.a(sub, 0x80, "W"); self.a(sub, 0x40, "W")
        elif code not in (5, 6):
            raise ScummError(f"Roomops: unknown subcode {sub:#x}")

    def verb_ops(self, op):
        self.a(op, 0x80, "B")
        while True:
            sub = self.byte()
            if sub == 0xFF:
                return
            code = sub & 0x1F
            if code in (0x1, 0x14):
                self.a(sub, 0x80, "W")
            elif code == 0x2:
                self.string("verb")
            elif code in (0x3, 0x4, 0x10, 0x12, 0x17):
                self.a(sub, 0x80, "B")
            elif code == 0x5:
                self.a(sub, 0x80, "W"); self.a(sub, 0x40, "W")
            elif code == 0x16:
                self.a(sub, 0x80, "W"); self.a(sub, 0x40, "B")
            elif code not in (0x6, 0x7, 0x8, 0x9, 0x11, 0x13):
                raise ScummError(f"Verbops: unknown subcode {sub:#x}")

    def save_load_vars(self, op=None):
        self.byte()
        while True:
            d = self.byte()
            if d == 0:
                return
            code = d & 0x1F
            if code == 0x01:
                self.var(); self.var()
            elif code == 0x02:
                self.a(d, 0x80, "B"); self.a(d, 0x40, "B")
            elif code == 0x03:
                self.string("file")
            elif code in (0x04, 0x1F):
                return

    def expression(self, op=None):
        self.var()
        while True:
            i = self.byte()
            if i == 0xFF:
                return
            code = i & 0x1F
            if code == 0x1:
                self.var_or_word(i & 0x80)
            elif code == 0x6:
                self.step()                                           # embedded command
            elif not 2 <= code <= 5:
                raise ScummError(f"expression: unknown code {i:#x}")


def _ops(codes, spec):
    return {c: spec for c in codes}


# Opcode → parameters (descumm.cpp, next_line_V345, version 4). Abbreviations:
# S target variable, B/W variable or byte/word (opcode bits 0x80, 0x40, 0x20
# in order), b/w fixed byte/word, v variable, L list, J jump target.
# Special cases are methods of the decoder.
OPCODES = {
    **_ops((0x00, 0xA0, 0x80, 0xC0, 0x20), ""),                     # stop, break, endCutscene, stopMusic
    **_ops((0x01, 0x21, 0x41, 0x61, 0x81, 0xA1, 0xC1, 0xE1), "BWW"),  # putActor
    **_ops((0x15, 0x55, 0x95, 0xD5), "SWW"),                         # actorFromPos
    **_ops((0x03, 0x83, 0x06, 0x86, 0x56, 0xD6, 0x63, 0xE3, 0x6C, 0xEC, 0x71, 0xF1,
            0x3B, 0xBB, 0x68, 0xE8, 0x7B, 0xFB, 0x7C, 0xFC, 0x31, 0xB1, 0x22, 0xA2,
            0x16, 0x96, 0x67, 0xE7), "SB"),                           # Var = f(Byte)
    **_ops((0x38, 0xB8, 0x04, 0x84, 0x08, 0x88, 0x48, 0xC8, 0x44, 0xC4, 0x78, 0xF8), "vWJ"),
    **_ops((0x28, 0xA8), "vJ"),                                       # if var ==/!= 0
    **_ops((0x05, 0x45, 0x85, 0xC5, 0x25, 0x65, 0xA5, 0xE5), "WWW"),  # drawObject (v4)
    **_ops((0x07, 0x47, 0x87, 0xC7), "WB"),                          # setState
    **_ops((0x09, 0x49, 0x89, 0xC9), "BW"),                          # faceActor
    **_ops((0x0A, 0x8A, 0x2A, 0xAA, 0x4A, 0xCA, 0x6A, 0xEA), "BL"),  # startScript
    **_ops((0x0B, 0x4B, 0x8B, 0xCB), "SWW"),                         # getVerbEntryPoint
    **_ops((0x0C, 0x8C), Decoder.resource),
    **_ops((0x0D, 0x4D, 0x8D, 0xCD), "BBb"),                         # walkActorToActor
    **_ops((0x0F, 0x8F, 0x2F, 0x4F, 0x6F, 0xAF, 0xCF, 0xEF), "WBJ"),  # if getState
    **_ops((0x10, 0x90), "SW"),                                      # getObjectOwner
    **_ops((0x14, 0x94, 0xD8), Decoder.print_ego),
    **_ops((0x17, 0x97, 0x1A, 0x9A, 0x1B, 0x9B, 0x3A, 0xBA, 0x57, 0xD7,
            0x5A, 0xDA, 0x5B, 0xDB), "SW"),                           # Var op= x
    **_ops((0x46, 0xC6), "S"),                                       # Var++ / Var--
    0x18: "J",                                                       # goto
    **_ops((0x1D, 0x9D), "WLJ"),                                     # classOfIs
    **_ops((0x1E, 0x3E, 0x5E, 0x7E, 0x9E, 0xBE, 0xDE, 0xFE), "BWW"),  # walkActorTo
    **_ops((0x24, 0x64, 0xA4, 0xE4), Decoder.load_room_with_ego),
    0x2C: Decoder.cursor,
    0x40: "L",                                                       # cutscene
    **_ops((0x42, 0xC2), "BL"),                                      # chainScript
    **_ops((0x72, 0xF2, 0x62, 0xE2, 0x52, 0xD2, 0x1C, 0x9C, 0x3C, 0xBC,
            0x02, 0x82, 0x60, 0xE0), "B"),                            # one byte argument
    **_ops((0x66, 0xE6, 0x43, 0xC3, 0x23, 0xA3), "SW"),              # getClosestObjActor, getActorX/Y
    0xAE: Decoder.wait,
    **_ops((0x34, 0x74, 0xB4, 0xF4), "SWW"),                         # getDist
    **_ops((0x36, 0x76, 0xB6, 0xF6), "BW"),                          # walkActorToObject
    **_ops((0x37, 0x77, 0xB7, 0xF7), "WBL"),                         # startObject
    **_ops((0x19, 0x39, 0x59, 0x79, 0x99, 0xB9, 0xD9, 0xF9), Decoder.do_sentence),
    0xAC: Decoder.expression,
    **_ops((0x11, 0x51, 0x91, 0xD1), "BB"),                          # animateCostume
    0x27: Decoder.string_ops,
    **_ops((0x13, 0x53, 0x93, 0xD3), Decoder.actor_ops),
    **_ops((0x70, 0xF0), "Bbb"),                                     # lights
    **_ops((0x3F, 0x7F, 0xBF, 0xFF), Decoder.draw_box),
    0xCC: Decoder.pseudo_room,
    **_ops((0x33, 0x73, 0xB3, 0xF3), Decoder.room_ops),
    0x2E: Decoder.delay,
    **_ops((0x29, 0x69, 0xA9, 0xE9), "WB"),                          # setOwnerOf
    0x58: "b",                                                       # begin/endOverride
    0x4C: "L",                                                       # soundKludge (v4)
    0x98: "b",                                                       # systemOps
    **_ops((0x7A, 0xFA), Decoder.verb_ops),
    **_ops((0x2D, 0x6D, 0xAD, 0xED), "BB"),                          # putActorInRoom
    **_ops((0x54, 0xD4), Decoder.set_object_name),
    **_ops((0x5D, 0xDD), "WL"),                                      # setClass
    **_ops((0x35, 0x75, 0xB5, 0xF5, 0x3D, 0x7D, 0xBD, 0xFD), "SBB"),  # findObject, findInventory
    **_ops((0x26, 0xA6), Decoder.set_var_range),
    0x2B: "v",                                                       # delayVariable
    **_ops((0x0E, 0x4E, 0x8E, 0xCE), "BW"),                          # putActorAtObject
    **_ops((0x12, 0x92, 0x32, 0xB2, 0x6E, 0xEE, 0x50, 0xD0), "W"),    # panCameraTo, setCameraAt, stopObjectScript, pickupObject
    0x6B: "W",                                                       # debug
    **_ops((0x30, 0xB0), Decoder.matrix_ops),
    **_ops((0x1F, 0x5F, 0x9F, 0xDF), "BBJ"),                         # isActorInBox
    0xAB: Decoder.save_verbs,
    **_ops((0x5C, 0xDC), Decoder.old_room_effect),
    0xA7: Decoder.save_load_vars,
}


def _verb_table(block):
    """OC block (from block start): {verb: code offset} and code start (descumm:
    skipVerbHeader_V34)."""
    table, at = {}, 19
    while block[at]:
        verb = block[at]
        off, = struct.unpack_from("<H", block, at + 1)
        table[verb] = off
        at += 3
    return table, min(table.values()) if table else len(block)


def decode_block(block, ident):
    """Texts of a script block (with 6-byte header: size, tag)."""
    tag = block[4:6].decode("latin1")
    if tag == "OC":
        verbs, start = _verb_table(block)
    elif tag == "LS":
        verbs, start = {}, 7
    elif tag in ("SC", "EN", "EX"):
        verbs, start = {}, 6
    else:
        raise ScummError(f"{ident}: no script block ({tag!r})")
    try:
        raw = Decoder(block, start).run()
    except (IndexError, struct.error) as e:
        raise ScummError(f"{ident}: bytecode ends in the middle of a command") from e
    texts = []
    for k, (at, kind, parts) in enumerate(raw, 1):
        t = Text(f"{ident}#{k}", kind, parts, at)
        if verbs:
            owners = [v for v, off in verbs.items() if off <= at]
            t.verb = max(owners, key=lambda v: verbs[v]) if owners else None
        texts.append(t)
    return texts


def _directory(game_dir, tag):
    index = (Path(game_dir) / "000.LFL").read_bytes()
    for t, s, e in blocks(index, 0, len(index)):
        if t == tag:
            count, = struct.unpack_from("<H", index, s)
            return [struct.unpack_from("<BI", index, s + 2 + i * 5) for i in range(count)]
    raise ScummError(f"000.LFL without directory {tag}")


def iter_blocks(game_dir):
    """(identifier, block with header) for all script blocks of a copy:
    global scripts, room scripts, entry/exit, object code."""
    game_dir = Path(game_dir)
    files = sorted(game_dir.glob("DISK*.LEC"), key=lambda p: p.name.upper())
    if not files:
        raise ScummError(f"no DISK*.LEC in {game_dir}")
    disks = [bytes(b ^ XOR_KEY for b in p.read_bytes()) for p in files]

    room_blocks = {}                                                  # room → (data, LF start)
    for data in disks:
        for i in range(data[12]):                                     # FO block after the LE header
            room, lf = struct.unpack_from("<BI", data, 13 + i * 5)
            if data[lf + 4:lf + 6] == b"LF":
                room_blocks.setdefault(room, (data, lf))

    # Global scripts via the directory (offsets from room block + 8). Some
    # entries point nowhere (unused numbers) – those do not exist.
    for number, (room, offset) in enumerate(_directory(game_dir, "0S")):
        if not room or room not in room_blocks:
            continue
        data, lf = room_blocks[room]
        at = lf + 8 + offset
        size, = struct.unpack_from("<I", data, at)
        if data[at + 4:at + 6] == b"SC":
            yield f"s{number}", data[at:at + size]

    for room, (data, lf) in sorted(room_blocks.items()):
        size, = struct.unpack_from("<I", data, lf)
        ro = find(data, lf + 8, lf + size, "RO")
        if not ro:
            continue
        for tag, s, e in blocks(data, *ro):
            block = data[s - 6:e]
            if tag == "LS":
                yield f"r{room}.s{data[s]}", block
            elif tag in ("EN", "EX"):
                yield f"r{room}.{tag.lower()}", block
            elif tag == "OC":
                number, = struct.unpack_from("<H", block, 6)
                yield f"o{number}", block


def read_texts(game_dir):
    """{identifier: Text} for all scripts of a copy, plus the object names."""
    texts = {}
    for ident, block in iter_blocks(game_dir):
        for t in decode_block(block, ident):
            texts[t.ident] = t
        if block[4:6] == b"OC":
            name_at = block[18]
            raw = block[name_at:block.index(0, name_at)]
            texts[f"{ident}.name"] = Text(f"{ident}.name", "objectName", [raw.decode("cp437")], name_at)
    return texts


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("game_dir", help="directory of the copy of the original game (DISK01.LEC …)")
    ap.add_argument("--filter", help="only identifiers that start like this (e.g. o498, r38.)")
    ap.add_argument("--grep", help="only texts that contain this regular expression")
    args = ap.parse_args()
    texts = read_texts(args.game_dir)
    for ident, t in texts.items():
        if args.filter and not ident.startswith(args.filter):
            continue
        shown = t.render()
        if args.grep and not re.search(args.grep, shown, re.I):
            continue
        verb = f" [Verb {t.verb}]" if t.verb is not None else ""
        print(f"{ident:18} {t.kind:13}{verb} {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
