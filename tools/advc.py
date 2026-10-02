#!/usr/bin/env python3
"""advc – Adventure-Compiler für Pocket Adventure FX.

Übersetzt die Spielbeschreibung (game/*.adv) samt Grafiken und Musik in
  * einen Datenblock game.bin, der als einzige Ressource in den FX-Flash geht,
  * gamedata.h mit Record-Structs, Opcodes, IDs und einer Build-ID.

Beide Dateien entstehen im selben Lauf aus denselben Record-Definitionen
(RECORDS unten). Dadurch können Engine und Daten strukturell nicht
auseinanderlaufen; eine veraltete FX-Datei erkennt die Engine an der Build-ID.

Byte-Reihenfolge: little-endian wie der AVR, damit die Engine Records mit
FX::readDataObject() direkt in ihre Structs lesen kann. Einzige Ausnahme sind
Bild-Header (Breite/Höhe big-endian), deren Format FX::drawBitmap() vorgibt.

Sprache der .adv-Datei: siehe README.md, Abschnitt „Skriptsprache“.
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

# Textbox: 21 Zeichen à 6 px = 126 px plus 1 px Rand je Seite = 128 px.
TEXT_COLS = 21
TEXT_ROWS = 4
OPTION_COLS = 19   # plus Auswahlzeichen links und Pfeilspalte rechts
# Größenstufen für Figuren mit Tiefe (actor … depth), als Anteil der vollen Größe
DEPTH_LEVELS = [1.0, 0.82, 0.66, 0.52, 0.4, 0.3, 0.21, 0.13]
MAX_OPTIONS = 16  # höchstens gleichzeitig sichtbare Optionen (RAM der Engine: so viele wie nötig)
CHOICE_ROWS = 4   # sichtbare Zeilen der Optionsliste (weniger, wenn der volle Text der gewählten mehr Platz braucht)
# Display: 8 Textzeilen. Unten die Optionsliste (eine Zeile je Option), oben
# der volle Text der gewählten Option, falls er umbricht.
SCREEN_ROWS = 8
MAX_INVENTORY = 16

DIRS = {"right": 0, "left": 1, "front": 2}

# Texte der Engine selbst je Sprache (keine Spieltexte, die kommen aus der
# Originalkopie), Zeichensatz CP437. sound_on/sound_off/empty: höchstens 21
# Zeichen (eine Zeile). Mit „ui.<schlüssel>“ in say verwendbar, z. B. für
# Ausgänge zu Räumen, die diese Fassung nicht enthält.
UI_TEXT = {
    "en": {"sound_on": "A:Start  B:Sound on", "sound_off": "A:Start  B:Sound off", "empty": "(empty)",
           "not_included": "Not included in this version."},
    "de": {"sound_on": "A:Start  B:Ton an", "sound_off": "A:Start  B:Ton aus", "empty": "(leer)",
           "not_included": "In dieser Fassung nicht enthalten."},
    "fr": {"sound_on": "A:Jouer  B:Son oui", "sound_off": "A:Jouer  B:Son non", "empty": "(vide)",
           "not_included": "Pas inclus dans cette version."},
    "it": {"sound_on": "A:Gioca  B:Audio sì", "sound_off": "A:Gioca  B:Audio no", "empty": "(vuoto)",
           "not_included": "Non incluso in questa versione."},
    "es": {"sound_on": "A:Jugar  B:Sonido sí", "sound_off": "A:Jugar  B:Sonido no", "empty": "(vacío)",
           "not_included": "No incluido en esta versión."},
}
UI_REF = re.compile(r"ui\.(\w+)")

# Timer3 läuft mit F_CPU/8 = 2 MHz im CTC-Modus und toggelt den Pin bei jedem
# Compare-Match: f = 2 MHz / (2 * (OCR + 1)).
TIMER3_HALF_CLOCK = 1_000_000

# --------------------------------------------------------------------------
# Record-Definitionen: einzige Quelle für Packen (Python) und Structs (C++)
# --------------------------------------------------------------------------

RECORDS = {
    # Am Anfang von game.bin: je enthaltene Sprache ein LangEntry mit ihrem
    # Namen (für die Sprachauswahl) und ihrem GameHeader. Bilder, Musik und
    # Laufwege teilen sich die Sprachen; Texte, Verben, Objekte und Skripte
    # hat jede für sich.
    "LangDir": [("magic", "u16"), ("buildId", "u16"), ("count", "u8")],
    "LangEntry": [("name", "u24"), ("header", "u24")],
    "GameHeader": [
        ("verbCount", "u8"), ("verbs", "u24"),
        ("objectCount", "u8"), ("objects", "u24"),
        ("actorCount", "u8"), ("actors", "u24"),
        ("roomCount", "u8"), ("rooms", "u24"),
        ("musicCount", "u8"), ("music", "u24"),
        ("startScript", "u24"), ("cursor", "u24"),
        ("title", "u24"), ("titleMusic", "u8"),
        # Verb für Inventar-Klicks mit „walk“ (z. B. look), sonst NONE8
        ("inventoryVerb", "u8"),
        # Texte der Engine selbst (nicht aus dem Spiel): Titelzeile, leeres Inventar
        ("uiSoundOn", "u24"), ("uiSoundOff", "u24"), ("uiEmpty", "u24"),
    ],
    # prep: Verbindungswort für Zwei-Objekt-Sätze („mit“, „an“), sonst NONE24
    "VerbRec": [("name", "u24"), ("prep", "u24"), ("fallback", "u24")],
    # verbs zeigt auf eine Liste von VerbEntry, beendet mit verb == NONE8
    "ObjectRec": [("name", "u24"), ("verbs", "u24")],
    "VerbEntry": [("verb", "u8"), ("other", "u8"), ("script", "u24")],
    # object: Objekt, für das die Figur selbst der Hotspot ist (NONE8 = keins);
    # der Hotspot wandert dann mit der Figur.
    "ActorRec": [
        ("sprite", "u24"),
        ("stand", "u8"), ("walkFirst", "u8"), ("walkCount", "u8"),
        ("talk", "u8"), ("front", "u8"), ("frontTalk", "u8"),
        ("object", "u8"),
        # depth: Größenstufen für Räume mit Tiefe (NONE24 = immer gleich groß);
        # Tabelle: u8 Anzahl, je Stufe u8 Mindestgröße (0–255) + u24 Sprite
        ("depth", "u24"),
    ],
    # matrix: boxCount × boxCount Bytes, Eintrag [von][nach] = nächste Box auf
    # dem kürzesten Weg (NONE8 = unerreichbar), vom Compiler vorberechnet.
    "RoomRec": [
        ("background", "u24"), ("width", "u16"), ("height", "u8"),
        ("boxCount", "u8"), ("boxes", "u24"), ("matrix", "u24"),
        ("placeCount", "u8"), ("places", "u24"),
        ("entry", "u24"),
    ],
    # Begehbares Viereck wie in SCUMM: Ecken oben links, oben rechts, unten
    # rechts, unten links. Darf zu einer Linie oder einem Punkt entarten.
    "BoxRec": [
        ("ulx", "u16"), ("uly", "u8"), ("urx", "u16"), ("ury", "u8"),
        ("lrx", "u16"), ("lry", "u8"), ("llx", "u16"), ("lly", "u8"),
        # Figurengröße (255 = voll) an der oberen und unteren Kante; dazwischen
        # linear nach y – wie die Skalierungsstufen des Originals (SA-Block)
        ("scaleTop", "u8"), ("scaleBottom", "u8"),
    ],
    # Ein Objekt an einer Stelle im Raum. image == NONE24: nur Hotspot.
    "PlaceRec": [
        ("object", "u8"), ("x", "u16"), ("y", "u8"), ("w", "u8"), ("h", "u8"),
        ("walkX", "u16"), ("walkY", "u8"), ("face", "u8"),
        ("image", "u24"), ("frames", "u8"), ("speed", "u8"),
    ],
    "TrackRec": [("count", "u16"), ("loop", "u8")],
    "NoteRec": [("ocr", "u16"), ("ms", "u8")],
}

SIZES = {"u8": 1, "u16": 2, "u24": 3}
CTYPES = {"u8": "uint8_t", "u16": "uint16_t", "u24": "__uint24"}


def record_size(name):
    return sum(SIZES[t] for _, t in RECORDS[name])


# Opcodes: (Name, Operanden). Operandtypen wie oben, 'addr' = u24-Sprungziel.
OPCODES = [
    ("END", []),
    ("SAY", ["u8", "u24"]),          # actor|NONE8=Erzähler, string
    ("WALK", ["u8", "u16", "u8"]),   # actor, x, y – blockiert bis Ankunft
    ("PUT", ["u8", "u16", "u8"]),    # actor, x, y – setzt ihn in den aktuellen Raum
    ("FACE", ["u8", "u8"]),          # actor, dir
    ("SET", ["u8"]),                 # flag
    ("CLEAR", ["u8"]),               # flag
    ("JUNLESS", ["u8", "u8", "u24"]),  # cond-kind, index, ziel: springt, wenn Bedingung FALSCH
    ("JMP", ["u24"]),
    ("PICKUP", ["u8"]),              # object → Inventar
    ("LOSE", ["u8"]),                # object aus dem Inventar entfernen (verbraucht)
    ("STATE", ["u8", "u8"]),         # object, zustand (Bild-Framegruppe)
    ("HIDE", ["u8"]),
    ("SHOW", ["u8"]),
    ("MUSIC", ["u8"]),               # track|NONE8=stop
    ("WAIT", ["u8"]),                # frames
    ("CHOICES", []),                 # neue Dialogauswahl beginnen
    ("OPTION", ["u24", "u24"]),      # text, ziel: Option anbieten (davor ggf. Bedingungscode)
    ("ASK", []),                     # Auswahl zeigen und warten; ohne Optionen weiter
    ("ROOM", ["u8", "u16", "u8", "u8"]),  # raum, x|0xFFFF, y, dir: laden, Spielfigur setzen, entry aufrufen
    ("CARD", ["u24", "u8"]),         # Vollbild, Musik|NONE8: bis die Musik endet (ohne: 3 s) oder A
    ("WAITR", ["u16", "u16"]),       # zufällig min … max Frames warten
    ("JUNLESSPOS", ["u8", "u8", "u16", "u24"]),  # POS_Y|POS_GT|COND_NOT, actor, wert, ziel: springt, wenn der Vergleich nicht gilt
    ("START", ["u24"]),              # Hintergrundablauf starten (ersetzt einen laufenden)
    ("STOP", ["u24"]),               # diesen Hintergrundablauf beenden
    ("RANDOM", ["u8"]),              # n, dann n × u24 Ziele: springt zu einem davon (gleichverteilt)
    ("SETSTR", ["u8", "u24"]),       # platz, text: String-Variable setzen
    ("SETCHAR", ["u8", "u8", "u8"]), # platz, stelle, zeichen ('@' wird nicht gezeigt)
    ("LET", ["u8", "u8"]),           # variable, wert
    ("FLASH", ["u8"]),               # frames: Bild blinkt invertiert (wartet)
    ("PAN", ["u16"]),                # Kamera zu x schwenken (wartet), bis die Spielfigur wieder gesetzt wird
    ("COSTUME", ["u8", "u8"]),       # actor, wie-actor: mit dessen Grafik zeigen
    ("PLAY", ["u24"]),               # Zwischensequenz als Vordergrundskript starten (aus einem Ablauf)
    ("CALL", ["u24"]),               # Unterprogramm (sub) aufrufen
    ("LETR", ["u8", "u8", "u8"]),    # variable, min, max: Zufallswert
    ("SETCHARV", ["u8", "u8", "u8"]),  # platz, stelle, variable: Zeichen aus einer Variablen
    ("ADD", ["u8", "u8"]),           # variable, summand (Zweierkomplement, Ergebnis mod 256)
    ("JUNLESSV", ["u8", "u8", "u8", "u24"]),  # VAR_*|COND_NOT, variable, wert, ziel
    ("REMOVE", ["u8"]),              # actor aus dem Raum nehmen (z. B. hinter einer Tür)
    ("HALT", ["u8"]),                # actor bleibt stehen, wo er gerade ist
]
OP = {name: i for i, (name, _) in enumerate(OPCODES)}

# Bedingungsarten für JUNLESS und Dialogoptionen
COND_FLAG = 0x00
COND_HAS = 0x01
COND_OPEN = 0x02
COND_HOVER = 0x03     # Mauszeiger liegt auf dem Objekt
POS_Y, POS_GT = 0x01, 0x02   # JUNLESSPOS: y statt x, > statt <
VERB_ANY = 0xFE      # VerbEntry.verb für „on other“ (alle übrigen Verben außer Gehe zu)
VAR_CMP = {"=": 0x00, "<": 0x01, ">": 0x02}   # Vergleich in JUNLESSV
WALK_DIRECT = 0xFFFF  # PlaceRec.walkX: Skript startet sofort (place … direct)
COND_NOT = 0x80



class CompileError(Exception):
    pass


# --------------------------------------------------------------------------
# Binär-Ausgabe mit Labels und Fixups
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
            raise CompileError(f"interner Fehler: Label doppelt: {label}")
        self.labels[label] = self.here()

    def put(self, typ, value):
        n = SIZES[typ]
        if isinstance(value, str):
            self.fixups.append((self.here(), value))
            value = 0
        if not 0 <= value < (1 << (8 * n)):
            raise CompileError(f"Wert {value} passt nicht in {typ}")
        self.data += value.to_bytes(n, "little")

    def record(self, record_name, /, **fields):
        spec = RECORDS[record_name]
        if set(fields) != {f for f, _ in spec}:
            raise CompileError(f"interner Fehler: Felder für {record_name}: {sorted(fields)}")
        for f, t in spec:
            self.put(t, fields[f])

    def raw(self, b):
        self.data += b

    def resolve(self):
        for off, label in self.fixups:
            if label not in self.labels:
                raise CompileError(f"interner Fehler: Label fehlt: {label}")
            self.data[off:off + 3] = self.labels[label].to_bytes(3, "little")


# --------------------------------------------------------------------------
# Bilder im FX-Format (identisch zu fxdata-build.py)
# --------------------------------------------------------------------------

def encode_image(path, mask=False):
    """Kodiert ein PNG; Framegröße aus dem Dateinamen: name_WxH.png."""
    m = re.search(r"_(\d+)x(\d+)$", path.stem)
    img = Image.open(path).convert("RGBA")
    frame = (int(m.group(1)), int(m.group(2))) if m else None
    return encode_pil(img, path.name, frame, mask)


def encode_pil(img, name, frame=None, mask=False):
    """Kodiert ein Bild wie fxdata-build.py: Header (w, h big-endian), danach
    je Frame spaltenweise Bytes pro 8-Pixel-Zeile; bei Transparenz folgt jedem
    Byte sein Maskenbyte. mask=True erzwingt die Maske auch bei deckenden
    Bildern – für alles, was die Engine mit dbmMasked zeichnet."""
    img = img.convert("RGBA")
    fw, fh = frame or img.size
    if img.width % fw or img.height % fh:
        raise CompileError(f"{name}: Bildgröße ist kein Vielfaches von {fw}x{fh}")
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
# Musik
# --------------------------------------------------------------------------

def parse_inline_notes(spec):
    notes = []
    for tok in spec.split():
        try:
            hz, ms = tok.split(":")
            notes.append((int(hz), int(ms)))
        except ValueError:
            raise CompileError(f"Note '{tok}' nicht im Format Hz:ms")
    return notes


def encode_track(notes):
    """Frequenz/Dauer → (Timer3-OCR, ms ≤ 255). Lange Noten werden geteilt;
    der Player setzt bei gleichem OCR den Zähler nicht zurück, es entsteht
    also keine hörbare Naht."""
    out = []
    for hz, ms in notes:
        if hz < 0 or ms <= 0:
            raise CompileError(f"ungültige Note {hz}:{ms}")
        ocr = 0 if hz == 0 else round(TIMER3_HALF_CLOCK / hz) - 1
        if not 0 <= ocr <= 0xFFFF:
            raise CompileError(f"Frequenz {hz} Hz außerhalb des Timer-Bereichs")
        while ms > 0:
            chunk = min(ms, 255)
            out.append((ocr, chunk))
            ms -= chunk
    return out


# --------------------------------------------------------------------------
# Laufflächen: Nachbarschaft und Wegfindungsmatrix
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
    """Punkt im (konvexen) Viereck; entartete Vierecke haben kein Inneres."""
    signs = [_cross(quad[i], quad[(i + 1) % 4], p) for i in range(4) if quad[i] != quad[(i + 1) % 4]]
    return len(signs) >= 3 and (all(s >= 0 for s in signs) or all(s <= 0 for s in signs))


def box_distance(q1, q2):
    """Kleinster Abstand zweier Laufflächen (0 bei Berührung/Überlappung)."""
    edges1 = [(q1[i], q1[(i + 1) % 4]) for i in range(4)]
    edges2 = [(q2[i], q2[(i + 1) % 4]) for i in range(4)]
    if any(_segments_intersect(a, b, c, d) for a, b in edges1 for c, d in edges2):
        return 0.0
    if any(_inside(q2, p) for p in q1) or any(_inside(q1, p) for p in q2):
        return 0.0
    return min(min(_seg_point_dist(c, d, p) for c, d in edges2 for p in q1),
               min(_seg_point_dist(a, b, p) for a, b in edges1 for p in q2))


# Laufflächen, die sich bis auf diesen Abstand nähern, gelten als verbunden.
# Ecken, die im Original aufeinanderliegen, haben Abstand 0; die Toleranz
# fängt nur Rundungen in handgeschriebenen Räumen ab.
BOX_TOUCH = 0.6


def box_matrix(quads):
    """Nächste-Box-Matrix per Breitensuche: m[von][nach] = erster Schritt."""
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
        return CompileError(f"Zeile {self.no}: {msg}")


def strip_comment(raw):
    """Schneidet einen Kommentar ab. '#' beginnt ihn nur am Wortanfang und
    außerhalb von Anführungszeichen – in Textverweisen wie r38.s203#1 ist es
    Teil des Worts."""
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
            raise CompileError(f"Zeile {no}: {e}")
        if toks:
            lines.append(Line(no, toks))
    return lines


def with_lead_in(melody, lead):
    """Setzt die Melodie erst nach einer Pause ein (wie die Kapitelkarte), spielt
    bis dahin die Noten einer Begleitstimme – der Lautsprecher ist einstimmig,
    im Original laufen die Stimmen nebeneinander. Länge und Takt bleiben."""
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
    """Figurengröße (1–255) an oberer und unterer Kante einer Original-Box:
    fest, oder bei 0x8000 | n nach Stufe n linear über y (ScummVM getScale)."""
    ys = [y for _, y in box.corners]
    if not box.scale & 0x8000:
        v = max(1, min(255, box.scale))
        return v, v
    s1, y1, s2, y2 = slots[box.scale & 0x7FFF]
    if y1 == y2:
        raise CompileError(f"Skalierungsstufe {box.scale & 0x7FFF} ungültig")

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
        self.vars = {}       # Zahlenvariablen (0…255): Name → Nummer
        self.defaults = {}   # verb id → script AST
        self.start_room = None
        self.cursor = None
        self.title = None
        self.title_music = None
        self.cards = {}      # id → {"room", "threshold", "music", "line"}
        self.routines = {}   # id → (Zeile, AST): Hintergrundabläufe
        self.cutscenes = {}  # id → (Zeile, AST): Szenen, die ein Ablauf mit play startet
        self.subs = {}       # id → (Zeile, AST): Unterprogramme (call)
        self.numbers = {}    # Variable → Nummer im Original (Code 4 in Texten)
        self.strings = {}    # id → {"original": Nummer, "slot", "line"}: String-Variablen
        self.string_refs = {}  # id → Textverweise aus setstring (für die Länge)
        self.inventory_verb = None
        self.original_dir = None   # Grafiken, Laufwege, Musik: aus der ersten Kopie
        self.languages = []        # TextSource je angegebener Kopie (Sprachfassung)
        self._original_rooms = None

    def original_room(self, number, ln):
        """Raum aus den Originaldaten (einmal geladen, dann gecacht)."""
        if self.original_dir is None:
            raise ln.err("braucht die Originaldaten: advc.py --original <Verzeichnis> "
                         "(im Makefile: make ORIGINAL=<Verzeichnis>)")
        if self._original_rooms is None:
            import scumm_v4
            try:
                self._original_rooms = scumm_v4.read_rooms(self.original_dir)
            except scumm_v4.ScummError as e:
                raise ln.err(f"Originaldaten: {e}")
        if number not in self._original_rooms:
            raise ln.err(f"Raum {number} gibt es in den Originaldaten nicht")
        return self._original_rooms[number]

    def original_room_of(self, game_dir, number, ln):
        """Raum aus einer bestimmten Kopie (Bilder, die je Sprache verschieden
        sind, z. B. Kapitelkarten mit eingezeichnetem Text)."""
        import scumm_v4
        cache = self.__dict__.setdefault("_rooms_by_dir", {})
        if game_dir not in cache:
            try:
                cache[game_dir] = scumm_v4.read_rooms(game_dir)
            except scumm_v4.ScummError as e:
                raise ln.err(f"Originaldaten: {e}")
        if number not in cache[game_dir]:
            raise ln.err(f"Raum {number} gibt es in {game_dir} nicht")
        return cache[game_dir][number]

    def original_costume(self, number, ln):
        self.original_room(1, ln)  # prüft Verzeichnis und lädt die Räume
        import scumm_v4
        try:
            return scumm_v4.read_costume(self.original_dir, number)
        except scumm_v4.ScummError as e:
            raise ln.err(f"Originaldaten: {e}")


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

    # ---- Top-Level ------------------------------------------------------

    def parse(self):
        while (ln := self.next()) is not None:
            kw, *args = ln.tokens
            handler = getattr(self, f"top_{kw}", None)
            if handler is None:
                raise ln.err(f"unbekannte Anweisung '{kw}'")
            handler(ln, args)

    def new_id(self, table, ln, ident, what):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", ident):
            raise ln.err(f"ungültiger Bezeichner '{ident}'")
        if ident in table:
            raise ln.err(f"{what} '{ident}' doppelt definiert")
        return ident

    def top_verb(self, ln, args):
        if len(args) not in (2, 4) or (len(args) == 4 and args[2] != "prep"):
            raise ln.err("Syntax: verb <id> <text> [prep <text>]  (Texte als Verweis, z. B. s22#10)")
        if args[0] == "other":
            raise ln.err("'other' ist reserviert (on other = alle übrigen Verben)")
        vid = self.new_id(self.g.verbs, ln, args[0], "Verb")
        self.g.verbs[vid] = {"name": self.text_ref(ln, args[1]),
                             "prep": self.text_ref(ln, args[3]) if len(args) == 4 else None,
                             "index": len(self.g.verbs)}

    def top_default(self, ln, args):
        if len(args) != 1:
            raise ln.err("Syntax: default <verb>")
        verb = self.ref(self.g.verbs, ln, args[0], "Verb")
        if verb in self.g.defaults:
            raise ln.err(f"default {verb} doppelt")
        self.g.defaults[verb] = self.block(("end",))[0]

    def top_music(self, ln, args):
        if len(args) >= 3 and args[1] == "original":
            self.original_music(ln, args)
            return
        if len(args) < 3 or args[1] != "notes":
            raise ln.err('Syntax: music <id> notes "<Hz:ms …>" [loop] '
                         '| music <id> original <sound> [channel N] [start MS]')
        mid = self.new_id(self.g.music, ln, args[0], "Musik")
        loop = args[3:] == ["loop"]
        if args[3:] not in ([], ["loop"]):
            raise ln.err(f"unerwartet: {args[3:]}")
        notes = parse_inline_notes(args[2])
        self.g.music[mid] = {"notes": encode_track(notes), "loop": loop, "index": len(self.g.music)}

    def original_music(self, ln, args):
        """Musik aus einer Original-Soundressource: die Melodiestimme der
        AdLib-Fassung (die PC-Speaker-Fassung ist bei den Raummusiken nur ein
        Platzhalter). Schleife wie im Original."""
        import scumm_v4
        mid = self.new_id(self.g.music, ln, args[0], "Musik")
        number = self.num(ln, args[2])
        opts = self.keyvals(ln, args[3:], {"channel": 1, "start": 1, "lead": 1})
        self.g.original_room(1, ln)  # prüft das Originalverzeichnis
        try:
            sound = scumm_v4.read_sound(self.g.original_dir, number)
            if "AD" not in sound:
                raise scumm_v4.ScummError(f"Sound {number} hat keine AdLib-Fassung")
            notes, loop, _ = scumm_v4.adlib_melody(sound["AD"], opts.get("channel", [None])[0])
            if "lead" in opts:
                lead, _, _ = scumm_v4.adlib_melody(sound["AD"], opts["lead"][0])
                notes = with_lead_in(notes, lead)
        except scumm_v4.ScummError as e:
            raise ln.err(f"Originaldaten: {e}")
        # start <ms>: Vorlauf überspringen (z. B. den langen Klangteppich vor
        # dem Titelthema, den ein einstimmiger Lautsprecher nicht wiedergibt).
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
            raise ln.err(f"start {opts['start'][0]}: danach bleibt keine Note")
        self.g.music[mid] = {"notes": encode_track(notes), "loop": loop, "index": len(self.g.music)}

    def top_actor(self, ln, args):
        # actor <id> sprite "<png>" stand N walk A B talk N front N fronttalk N
        # actor <id> costume <nr> [scale S] [palette RAUM] [dark D] [outline O]
        # actor <id> invisible
        args = args[:1] + [None] + args[1:]   # Platz des früheren Namens: Indizes bleiben
        if len(args) >= 4 and args[2] == "costume":
            self.costume_actor(ln, args)
            return
        if len(args) == 3 and args[2] == "invisible":
            # Sprecher ohne eigene Figur, z. B. Leute, die Teil der Kulisse sind
            aid = self.new_id(self.g.actors, ln, args[0], "Actor")
            self.g.actors[aid] = {
                "sprite": ("pil", "invisible", Image.new("RGBA", (1, 1), (0, 0, 0, 0))),
                "stand": 0, "walk": [0, 1], "talk": 0, "front": 0, "fronttalk": 0,
                "index": len(self.g.actors), "line": ln,
            }
            return
        if len(args) < 4 or args[2] != "sprite":
            raise ln.err('Syntax: actor <id> sprite "<png>" stand N walk ERST ANZAHL talk N front N fronttalk N '
                         '| actor <id> costume <nr> [scale S] [palette RAUM] [dark D] [outline O] | actor <id> invisible')
        aid = self.new_id(self.g.actors, ln, args[0], "Actor")
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
        # Standardrand 2 px: Mit 1 px gehen Figuren in hellen, detailreichen
        # Räumen wie der Küche im Raster der Kulisse unter.
        opts = self.keyvals(ln, toks, {"scale": 1, "palette": 1, "dark": 1, "outline": 1, "object": 1, "depth": 0})
        return (opts.get("scale", [0.55])[0], opts.get("palette", [38])[0],
                opts.get("dark", [45])[0], opts.get("outline", [2])[0],
                opts.get("object", [None])[0], "depth" in opts)

    def costume_actor(self, ln, args):
        """Actor aus einem Original-Kostüm: Stehen, Laufzyklus, Reden, vorne,
        vorne reden – alle mit Blick nach rechts; links spiegelt die Engine."""
        import scumm_v4 as sv
        aid = self.new_id(self.g.actors, ln, args[0], "Actor")
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
            strip, fw, fh = orig.costume_frames(costume, palette, seqs, scale, dark, outline)
        except ValueError as e:
            raise ln.err(str(e))
        n = len(seqs)
        levels = []
        if depth:
            # Tiefe: dieselben Frames in kleineren Stufen, die Engine wählt je
            # nach Größe der Lauffläche (Skalierung zur Laufzeit wäre zu teuer)
            for f in DEPTH_LEVELS[1:]:
                try:
                    st, w, h = orig.costume_frames(costume, palette, seqs, scale * f, dark, outline)
                except ValueError as e:
                    raise ln.err(str(e))
                levels.append((f, ("pil", f"costume{number}_{round(scale * f, 3)}_{dark}_{outline}", st, (w, h))))
        self.g.actors[aid] = {
            "sprite": ("pil", f"costume{number}_{scale}_{dark}_{outline}", strip, (fw, fh)),
            "stand": 0, "walk": [1, steps], "talk": n - 3, "front": n - 2, "fronttalk": n - 1,
            "object": obj, "index": len(self.g.actors), "line": ln, "levels": levels,
        }

    def costume_place_image(self, ln, number, scales, palette_room, dark, outline):
        """Animiertes Raumobjekt aus einem Kostüm (Stehen-Animation), eine
        Framegruppe je Zustand; die Zustände unterscheiden sich in der Größe.
        Alle Frames bekommen die gemeinsame Größe, Fußpunkt unten mittig."""
        import scumm_v4 as sv
        costume = self.g.original_costume(number, ln)
        palette = self.g.original_room(palette_room, ln).palette
        stand = [sv.INIT, sv.STAND]
        steps = orig.cycle_length(costume, stand, sv.RIGHT)
        seqs = [(stand, sv.RIGHT, k) for k in range(steps)]
        strips = [orig.costume_frames(costume, palette, seqs, sc, dark, outline) for sc in scales]
        fw = max(w for _, w, _ in strips)
        fh = max(h for _, _, h in strips)
        out = Image.new("RGBA", (fw * steps * len(strips), fh), (0, 0, 0, 0))
        for si, (strip, w, h) in enumerate(strips):
            for k in range(steps):
                frame = strip.crop((k * w, 0, (k + 1) * w, h))
                out.paste(frame, ((si * steps + k) * fw + (fw - w) // 2, fh - h))
        key = f"costume{number}_" + "_".join(map(str, scales)) + f"_{dark}_{outline}"
        return ("pil", key, out, (fw, fh)), steps, fw, fh

    def top_object(self, ln, args):
        if len(args) != 2:
            raise ln.err("Syntax: object <id> <name>  (Name als Verweis, z. B. o498.name)")
        oid = self.new_id(self.g.objects, ln, args[0], "Objekt")
        obj = {"name": self.text_ref(ln, args[1]), "verbs": [], "index": len(self.g.objects), "line": ln}
        self.g.objects[oid] = obj
        # Verb-Handler werden erst nach dem Einlesen aller Objekte aufgelöst,
        # weil 'on use <anderes_objekt>' vorwärts verweisen darf.
        while True:
            sub = self.next()
            if sub is None:
                raise ln.err(f"object {oid}: 'end' fehlt")
            kw, *a = sub.tokens
            if kw == "end" and not a:
                break
            if kw != "on" or len(a) not in (1, 2):
                raise sub.err("in object erwartet: on <verb> [<objekt>] … end")
            body = self.block(("end",))[0]
            obj["verbs"].append({"verb": a[0], "other": a[1] if len(a) == 2 else None,
                                 "body": body, "line": sub})

    def top_room(self, ln, args):
        syntax = ('Syntax: room <id> bg "<png>" | room <id> original <nr> [tone S W] [contrast K] [channel C] '
                  '| room <id> blank')
        if len(args) < 2 or args[1] not in ("bg", "original", "blank") or (args[1] != "blank" and len(args) < 3):
            raise ln.err(syntax)
        rid = self.new_id(self.g.rooms, ln, args[0], "Raum")
        room = {"boxes": [], "places": [], "entry": [], "index": len(self.g.rooms), "line": ln,
                "original": None, "scale": 1.0, "dx": 0}
        if args[1] == "blank":
            # Schwarzes Bild ohne Laufflächen, z. B. für „Unterdessen …“ (s117)
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
            # height: höher als das Display (die Karte), die Kamera scrollt dann
            # auch senkrecht; sonst auf 64 px Höhe
            height = opts.get("height", [64])[0]
            if not 64 <= height <= 255:
                raise ln.err("height: 64…255")
            room["scale"] = height / source.height
            size = (orig.scaled(source.width, room["scale"]), height)
            # Schmaler als das Display (die Karte von Mêlée): mittig auf Schwarz,
            # Laufflächen und Objekte um denselben Versatz verschoben.
            room["dx"] = max(0, (128 - size[0]) // 2)
            # Objektbilder, die fest zur Kulisse gehören (z. B. die Piraten
            # in der SCUMM Bar), vor der Umrechnung auflegen.
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
                raise ln.err(f"room {rid}: 'end' fehlt")
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
                    raise sub.err("walkboxes original nur in einem Raum aus den Originaldaten")
                s = room["scale"]
                for box in room["original"].boxes:
                    room["boxes"].append(([(x * s + room["dx"], y * s) for x, y in box.corners], sub,
                                          box_scale(box, room["original"].scale_slots)))
            elif kw == "place":
                room["places"].append(self.place(sub, a, room))
            elif kw == "entry" and not a:
                room["entry"] = self.block(("end",))[0]
            else:
                raise sub.err(f"in room unbekannt: '{kw}'")

    def top_start(self, ln, args):
        """start <raum> oder ein start … end-Block (Startskript, das mit
        room <raum> at x y beginnt)."""
        if len(args) == 1:
            self.g.start_room = (args[0], ln)
        elif not args:
            self.g.start_room = self.block(("end",))[0]
        else:
            raise ln.err("Syntax: start <raum> | start … end")

    def top_inventoryverb(self, ln, args):
        if len(args) != 1:
            raise ln.err("Syntax: inventoryverb <verb>")
        self.g.inventory_verb = (args[0], ln)

    def top_cursor(self, ln, args):
        if len(args) != 1:
            raise ln.err('Syntax: cursor "<png>"')
        self.g.cursor = args[0]

    def top_routine(self, ln, args):
        """routine <id> … end: Ablauf, der neben dem Spiel läuft (z. B. der Koch,
        der in Abständen aus der Küche kommt). Er sperrt die Eingabe nicht und
        pausiert, solange ein Skript läuft (Zwischensequenz, Dialog)."""
        if len(args) != 1:
            raise ln.err("Syntax: routine <id>")
        rid = self.new_id(self.g.routines, ln, args[0], "Ablauf")
        body, _ = self.block(("end",))
        self.g.routines[rid] = (ln, body)

    def top_sub(self, ln, args):
        """sub <id> … end: Unterprogramm, aufgerufen mit call <id> – für
        Gesprächsteile, die das Original von mehreren Stellen anspringt."""
        if len(args) != 1:
            raise ln.err("Syntax: sub <id>")
        sid = self.new_id(self.g.subs, ln, args[0], "Unterprogramm")
        body, _ = self.block(("end",))
        self.g.subs[sid] = (ln, body)

    def top_cutscene(self, ln, args):
        """cutscene <id> … end: eine Szene, die ein Hintergrundablauf mit
        play <id> auslöst (z. B. die Warnungen der Piraten, wenn man der
        Ratte zu nahe kommt). Sie läuft wie ein Verbskript im Vordergrund."""
        if len(args) != 1:
            raise ln.err("Syntax: cutscene <id>")
        cid = self.new_id(self.g.cutscenes, ln, args[0], "Szene")
        body, _ = self.block(("end",))
        self.g.cutscenes[cid] = (ln, body)

    def top_number(self, ln, args):
        """number <variable> original <nr>: Variable, deren Wert Texte des
        Originals mit Code 4 (<4:nr>) als Zahl einsetzen (z. B. das Geld)."""
        if len(args) != 3 or args[1] != "original":
            raise ln.err("Syntax: number <variable> original <nr>")
        if args[0] in self.g.numbers:
            raise ln.err(f"number {args[0]} doppelt")
        self.g.numbers[args[0]] = self.num(ln, args[2])

    def top_string(self, ln, args):
        """string <id> original <nr>: String-Variable, die Texte des Originals
        mit Code 7 (<7:nr>) einsetzen, z. B. der Name, den der Ausguck sich
        merkt. Inhalt per setstring/setchar."""
        if len(args) != 3 or args[1] != "original":
            raise ln.err("Syntax: string <id> original <nr>")
        sid = self.new_id(self.g.strings, ln, args[0], "String")
        self.g.strings[sid] = {"original": self.num(ln, args[2]), "slot": len(self.g.strings), "line": ln}

    def top_card(self, ln, args):
        """card <id> original <raum> [threshold N] [music <id>]: Vollbild aus
        einem Originalraum (Kapitelkarte). Der Inhalt ist Schrift: Zuschnitt auf
        den hellen Bereich, auf 128×64 eingepasst, Schwellwert auf die hellste
        Farbkomponente (Schrift in Blau o. Ä. bleibt lesbar). Das Bild kommt aus
        jeder Sprachfassung einzeln – der Text ist eingezeichnet."""
        if len(args) < 3 or args[1] != "original":
            raise ln.err("Syntax: card <id> original <raum> [threshold N] [music <id>]")
        cid = self.new_id(self.g.cards, ln, args[0], "Karte")
        opts = self.keyvals(ln, args[3:], {"threshold": 1, "music": 1})
        self.g.cards[cid] = {"room": self.num(ln, args[2]), "threshold": opts.get("threshold", [50])[0],
                             "music": opts.get("music", [None])[0], "line": ln}

    def top_title(self, ln, args):
        syntax = ('Syntax: title "<png>" [music <id>] | title original <raum> [overlay <objekt> X Y] '
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
            raise ln.err("title original braucht crop X0 Y0 X1 Y1")
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
            raise ln.err("crop liegt außerhalb des Bildes")
        width = orig.scaled(x1 - x0, 64 / (y1 - y0))
        if width > 128:
            raise ln.err(f"crop ist zu breit: wird {width} px statt höchstens 128")
        black, white = opts.get("tone", [30, 95])
        mono = orig.to_mono(img.crop((x0, y0, x1, y1)), (width, 64), black, white,
                            opts.get("contrast", [1.0])[0])
        canvas = Image.new("RGBA", (128, 64), (0, 0, 0, 255))
        canvas.paste(mono, ((128 - width) // 2, 0))
        self.g.title = ("pil", "title", canvas)
        self.g.title_music = (opts["music"][0], ln) if "music" in opts else None

    # ---- Hilfen ---------------------------------------------------------

    def num(self, ln, v):
        try:
            return int(v, 0)
        except ValueError:
            raise ln.err(f"Zahl erwartet, nicht '{v}'")

    def real(self, ln, v):
        try:
            return float(v)
        except ValueError:
            raise ln.err(f"Zahl erwartet, nicht '{v}'")

    def keyvals(self, ln, toks, arity, words=()):
        out = {}
        i = 0
        while i < len(toks):
            k = toks[i]
            if k not in arity:
                raise ln.err(f"unbekannte Option '{k}'")
            n = arity[k]
            if n is None:  # beliebig viele Zahlen bis zur nächsten Option
                n = 0
                while i + 1 + n < len(toks) and toks[i + 1 + n] not in arity:
                    n += 1
                if not n:
                    raise ln.err(f"'{k}' braucht mindestens einen Wert")
            vals = toks[i + 1:i + 1 + n]
            if len(vals) != n:
                raise ln.err(f"'{k}' braucht {n} Wert(e)")
            out[k] = [v if k in ("image", "face", "music", "object") or k in words else
                      self.real(ln, v) if k in ("contrast", "scale", "states") else self.num(ln, v) for v in vals]
            i += 1 + n
        return out

    def place(self, ln, a, room):
        # place <obj> at x y [w h] [image …] walkto x y face dir
        # place <obj> original <nr> [image …] [walkto x y] [face dir]
        # place decor at x y costume|image … – nur Bild, kein Hotspot
        syntax = ('Syntax: place <objekt> at x y [w h] [image "<png>" frames N speed N] walkto x y face <dir> '
                  '| place <objekt> original <nr> [walkto x y] [face <dir>]')
        if len(a) < 3 or a[1] not in ("at", "original"):
            raise ln.err(syntax)
        obj = a[0]
        w = h = None
        walk = face = None
        dx = 0   # Versatz schmaler Räume (nur für Koordinaten aus dem Original)
        if a[1] == "original":
            dx = room["dx"]
            if not room["original"]:
                raise ln.err("place … original nur in einem Raum aus den Originaldaten")
            number = self.num(ln, a[2])
            source = next((o for o in room["original"].objects if o.number == number), None)
            if source is None:
                raise ln.err(f"Objekt {number} gibt es im Originalraum {room['original'].number} nicht")
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
        if "picture" in opts:
            # Aufnehmbarer Gegenstand: sein Originalbild, ausgeschnitten aus der
            # Kulisse mit aufgelegtem Objekt. Die Kulisse selbst ist ohne ihn
            # umgerechnet; nimmt man ihn, verschwindet das Bild.
            if a[1] != "original":
                raise ln.err("picture nur mit place … original <nr>")
            src = room["original"]
            comp = room["composite"].copy()
            comp.paste(self.object_image(ln, src, number), self.object_pos(ln, src, number))
            mono = orig.to_mono(comp, *room["tone"])
            image = ("pil", f"room{src.number}_obj{number}", mono.crop((x, y, x + w, y + h)))
        if "door" in opts:
            # Tür: Zustand 0 = zu (wie die Kulisse), 1 = offen (Objektbild des
            # Originals aufgelegt) – zwei Bilder, je Zustand eins.
            if a[1] != "original":
                raise ln.err("door nur mit place … original <nr>")
            src = room["original"]
            comp = room["composite"].copy()
            closed = orig.to_mono(comp, *room["tone"]).crop((x, y, x + w, y + h))
            comp.paste(self.object_image(ln, src, number), self.object_pos(ln, src, number))
            opened = orig.to_mono(comp, *room["tone"]).crop((x, y, x + w, y + h))
            strip = Image.new("RGBA", (w * 2, h))
            strip.paste(closed, (0, 0))
            strip.paste(opened, (w, 0))
            image = ("pil", f"room{src.number}_door{number}", strip, (w, h))
        if "costume" in opts:
            # at x y ist hier der Fußpunkt; das Bild steht mittig darüber.
            image, frames, fw, fh = self.costume_place_image(
                ln, opts["costume"][0], opts.get("states", [room["scale"]]),
                opts.get("palette", [38])[0], opts.get("dark", [45])[0], opts.get("outline", [1])[0])
            x, y = x - fw // 2, y - fh + 1
        if "walkto" in opts:
            walk = opts["walkto"]
        if "direct" in opts:
            # Verbskripte starten beim Klick, ohne dass die Spielfigur erst
            # hinläuft; das Skript lässt sie selbst laufen (wie r28.s203, das
            # beim Klick auf die Küchentür sofort nach dem Koch sieht).
            if "walkto" in opts:
                raise ln.err("direct und walkto schließen sich aus")
            walk = [WALK_DIRECT, 0]
        if "face" in opts:
            if opts["face"][0] not in DIRS:
                raise ln.err(f"face: {'/'.join(DIRS)}")
            face = DIRS[opts["face"][0]]
        if obj == "decor":
            if not image:
                raise ln.err("place decor braucht ein Bild (image, costume oder picture)")
            walk, face = walk or [0, 0], 0 if face is None else face
        elif walk is None or face is None:
            raise ln.err("place braucht walkto und face")
        return {"object": obj, "x": x + dx, "y": y, "w": w, "h": h, "image": image,
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
            raise ln.err(f"Objekt {number} gibt es im Originalraum {source.number} nicht")
        return o.x, o.y

    def text_ref(self, ln, tok):
        """Spieltexte stehen nicht im Repo: nur Verweise auf die Originalkopie
        (oder ui.<schlüssel> für die eigenen Texte der Engine)."""
        m = UI_REF.fullmatch(tok)
        if m:
            if m.group(1) not in UI_TEXT["en"]:
                raise ln.err(f"Engine-Text {tok!r} unbekannt ({', '.join(UI_TEXT['en'])})")
            return tok
        if not REF.fullmatch(tok):
            raise ln.err(f"Text als Verweis auf die Originaldaten angeben (z. B. r38.s203#1, o498.name), "
                         f"nicht {tok!r}; Liste: tools/scumm_text.py <Kopie>")
        return tok

    def ref(self, table, ln, ident, what):
        if ident not in table:
            raise ln.err(f"{what} '{ident}' unbekannt")
        return ident

    # ---- Skriptblöcke ---------------------------------------------------

    def block(self, terminators):
        """Liest Anweisungen bis zu einem der Terminatoren. Liefert (AST, Terminator-Zeile)."""
        stmts = []
        while True:
            ln = self.next()
            if ln is None:
                raise CompileError(f"Dateiende: erwartet {' / '.join(terminators)}")
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
        """Bedingung: Teilbedingungen, mit „and“ verknüpft; Liste von Atomen."""
        atoms, part = [], []
        for t in toks + ["and"]:
            if t == "and":
                if not part:
                    raise ln.err("Bedingung: 'and' ohne Teilbedingung")
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
                raise ln.err("<variable> =|<|> <zahl>")
        if len(toks) == 4 and toks[1] in ("x", "y") and toks[2] in ("<", ">"):
            try:
                return ("pos", (toks[0], toks[1], toks[2], int(toks[3], 0)), neg)
            except ValueError:
                raise ln.err("<actor> x|y <|> <zahl>")
        raise ln.err("Bedingung: [not] <flag> | [not] has <objekt> | [not] <objekt> open | [not] hover <objekt> | "
                     "[not] <actor> x|y <|> <zahl> | [not] <variable> =|<|> <zahl>, mehrere mit 'and'")

    def random_block(self, ln):
        """random / case [gewicht] … / end: einer der Fälle, gleichverteilt
        oder nach Gewicht (wie getRandomNr im Original; ein leerer Fall ist
        erlaubt). Die Engine wählt einen Eintrag ihrer Sprungtabelle, ein Fall
        mit Gewicht n steht dort n-mal."""
        if len(ln.tokens) != 1:
            raise ln.err("Syntax: random, darunter 'case [gewicht]' je Fall, dann 'end'")

        def weight(line):
            if line is None or line.tokens[0] != "case" or len(line.tokens) > 2:
                raise ln.err("random: Einträge beginnen mit 'case [gewicht]'")
            w = self.num(line, line.tokens[1]) if len(line.tokens) == 2 else 1
            if not 0 < w < 256:
                raise line.err("case: Gewicht 1…255")
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
            raise ln.err("random: Gewichte zusammen höchstens 255")
        if len(cases) < 2:
            raise ln.err("random braucht mindestens zwei Fälle")
        return ("random", ln, cases)

    def choose(self, ln):
        options = []
        opt_line = self.next()
        while opt_line is not None and opt_line.tokens[0] == "option":
            t = opt_line.tokens
            if len(t) < 2:
                raise opt_line.err("Syntax: option <text> [if <bedingung>]")
            self.text_ref(opt_line, t[1])
            cond = None
            if len(t) > 2:
                if t[2] != "if":
                    raise opt_line.err("nach dem Optionstext nur 'if …'")
                cond = self.cond(opt_line, t[3:])
            body, term = self.block(("option", "end"))
            options.append({"text": t[1], "cond": cond, "body": body, "line": opt_line})
            if term.tokens[0] == "end":
                break
            self.i -= 1  # 'option' gehört zur nächsten Runde
            opt_line = self.next()
        else:
            raise ln.err("choose braucht mindestens eine 'option' und ein 'end'")
        if len(options) > 255:
            raise ln.err("höchstens 255 Optionen pro choose")
        return ("choose", ln, options)


# --------------------------------------------------------------------------
# Codegenerator
# --------------------------------------------------------------------------

class Compiler:
    def __init__(self, game):
        self.g = game
        self.b = Blob()
        self.in_entry = False
        self.in_routine = False
        self.strings = {}         # Text → Label, je Sprache neu
        self.label_no = 0
        self.images = {}
        self.src = None           # TextSource der Sprache, die gerade übersetzt wird
        self.text_max = 0         # längste Sprechblase/Option (Textpuffer der Engine)
        self.max_visible = 1      # gleichzeitig sichtbare Optionen (Auswahlpuffer der Engine)
        self.lang_ui = {}         # Sprache → Labels der Engine-Texte (UI_TEXT)

    def label(self, hint):
        self.label_no += 1
        return f"{hint}_{self.label_no}"

    def L(self, name):
        """Label eines sprachabhängigen Teils (Header, Verben, Skripte, Texte)."""
        return f"{self.src.code}:{name}"

    @staticmethod
    def encode_text(text):
        """Text → Bytes für die Engine: CP437, Platzhalter-Plätze als ein Byte
        (Platz + 1, siehe scumm_text.SLOT_BASE)."""
        enc = bytearray()
        for ch in text:
            if scumm_text.SLOT_BASE <= ord(ch) < scumm_text.SLOT_BASE + 0xFF:
                enc.append(ord(ch) - scumm_text.SLOT_BASE + 1)
                continue
            try:
                enc += ch.encode("cp437")
            except UnicodeEncodeError as e:
                raise CompileError(f"Zeichen nicht im Arduboy-Font (CP437): {text!r} ({e})")
        if 0 in enc:
            raise CompileError(f"NUL im String: {text!r}")
        return bytes(enc)

    def string(self, text):
        """Legt einen String (nullterminiert) an; Duplikate werden geteilt."""
        self.encode_text(text)   # früh prüfen, mit Zeilenbezug beim Aufrufer
        if text not in self.strings:
            self.strings[text] = self.label("str")
        return self.strings[text]

    def text_line(self, ln, ref):
        """Einzeiliger Spieltext (Verb, Name) der aktuellen Sprache als String."""
        try:
            return self.string(self.src.line(ref))
        except TextError as e:
            raise (ln.err(str(e)) if ln else CompileError(str(e)))

    def bubbles(self, ln, ref):
        """Sprechblasen eines Spieltexts in der aktuellen Sprache."""
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
        """Bild der Karte in der Sprache self.src (Label)."""
        c = self.g.cards[cid]
        room = self.g.original_room_of(self.src.dir, c["room"], c["line"])
        rgb = np.asarray(room.image().convert("RGB")).max(axis=2)
        ys, xs = np.nonzero(rgb > c["threshold"])
        if not len(xs):
            raise c["line"].err(f"Raum {c['room']}: nichts heller als {c['threshold']}")
        h_img, w_img = rgb.shape
        x0, y0 = max(0, xs.min() - 2), max(0, ys.min() - 2)
        x1, y1 = min(w_img, xs.max() + 3), min(h_img, ys.max() + 3)
        scale = min(128 / (x1 - x0), 64 / (y1 - y0))
        size = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale)))
        bright = Image.fromarray(rgb.astype(np.uint8)).crop((x0, y0, x1, y1)).resize(size, Image.BOX)
        mono = bright.point(lambda v: 255 if v > c["threshold"] else 0).convert("L")
        canvas = Image.new("RGBA", (128, 64), (0, 0, 0, 255))
        canvas.paste(Image.merge("RGBA", (mono, mono, mono, Image.new("L", size, 255))),
                     ((128 - size[0]) // 2, (64 - size[1]) // 2))
        return self.image(("pil", f"card_{cid}_{self.src.code}", canvas), c["line"])["label"]

    def image(self, src, ln, mask=False):
        """Bild aus einer PNG-Datei (Pfad) oder aus den Originaldaten
        (("pil", schlüssel, bild[, framegröße])); gleiche Quellen werden nur
        einmal abgelegt."""
        key = (src[1] if isinstance(src, tuple) else src, mask)
        if key not in self.images:
            if isinstance(src, tuple):  # ("pil", schlüssel, bild[, framegröße])
                data, w, h, frames = encode_pil(src[2], src[1], src[3] if len(src) > 3 else None, mask)
            else:
                path = self.g.base / src
                if not path.exists():
                    msg = f"Bild fehlt: {src}"
                    raise ln.err(msg) if ln else CompileError(msg)
                data, w, h, frames = encode_image(path, mask)
            self.images[key] = {"label": self.label("img"), "data": data, "w": w, "h": h,
                                "frames": frames, "source": src}
        return self.images[key]

    # ---- Referenzen ----

    def var(self, ln, name):
        err = ln.err if ln else CompileError
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            raise err(f"ungültiger Variablenname '{name}'")
        if name in self.g.flags:
            raise err(f"'{name}' ist schon ein Flag")
        if name not in self.g.vars:
            if len(self.g.vars) >= 255:
                raise err("mehr als 255 Variablen")
            self.g.vars[name] = len(self.g.vars)
        return self.g.vars[name]

    def flag(self, name):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            raise CompileError(f"ungültiger Flag-Name '{name}'")
        if name in self.g.vars:
            raise CompileError(f"'{name}' ist schon eine Variable")
        if name not in self.g.flags:
            if len(self.g.flags) >= 255:
                raise CompileError("mehr als 255 Flags")
            self.g.flags[name] = len(self.g.flags)
        return self.g.flags[name]

    def obj(self, ln, name):
        if name not in self.g.objects:
            raise ln.err(f"Objekt '{name}' unbekannt")
        return self.g.objects[name]["index"]

    def actor(self, ln, name):
        if name == "narrator":
            return NONE8
        if name not in self.g.actors:
            raise ln.err(f"Actor '{name}' unbekannt")
        return self.g.actors[name]["index"]

    def text_buffer(self):
        if self.text_max + 1 > 255:
            raise CompileError(f"Text mit {self.text_max} Zeichen zu lang für den Textpuffer (höchstens 254)")
        return self.text_max + 1

    def shown_len(self, text):
        """Länge eines Texts, wie die Engine ihn ausgibt: Platzhalter mit dem
        längsten Inhalt der Variablen."""
        n = len(text)
        for code, width in self.src.widths().items():
            n += text.count(code) * (width - 2)
        return n

    # ---- Aufruftiefe (Rücksprungstapel der Engine) ----

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
        """Rücksprünge, die ein Skript höchstens braucht: call und ROOM (das
        Entry-Skript des neuen Raums) je einen, plus was darin aufgerufen wird."""
        need = 0
        for _, ln, toks in self._children(stmts):
            if toks[0] == "call" and len(toks) == 2 and toks[1] in self.g.subs:
                if toks[1] in path:
                    raise ln.err(f"Unterprogramm '{toks[1]}' ruft sich selbst auf")
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

    ROUTINES = 2   # Hintergrundplätze der Engine (Script.cpp)

    def started(self, stmts, path=()):
        """Abläufe, die ein Skript (samt Unterprogrammen) starten kann."""
        out = set()
        for _, ln, toks in self._children(stmts):
            if toks[0] == "start" and len(toks) == 2:
                out.add(toks[1])
            elif toks[0] == "call" and len(toks) == 2 and toks[1] in self.g.subs and toks[1] not in path:
                out |= self.started(self.g.subs[toks[1]][1], path + (toks[1],))
        return out

    def check_routines(self):
        """Ein Raumwechsel beendet alle Abläufe; in einem Raum laufen also
        höchstens die, die sein Entry-Skript, die Verben seiner Objekte oder
        ein Inventarobjekt starten. Das dürfen nicht mehr als ROUTINES sein."""
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
                raise r["line"].err(f"Raum {rid}: bis zu {len(names)} Abläufe gleichzeitig "
                                    f"({', '.join(sorted(names))}), die Engine hat {self.ROUTINES} Plätze")

    def call_depth(self):
        g = self.g
        for rid, r in g.rooms.items():
            if self.changes_room(r["entry"]):
                raise r["line"].err(f"Raum {rid}: Entry-Skripte dürfen keinen Raum wechseln "
                                    f"(auch nicht über ein Unterprogramm)")
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
            raise CompileError(f"Unterprogramme zu tief geschachtelt ({need} Ebenen, höchstens 8)")
        return need

    def string_var(self, ln, sid):
        if sid not in self.g.strings:
            raise ln.err(f"String '{sid}' unbekannt (string <id> original <nr>)")
        return sid

    def string_widths(self, src):
        """Längster Inhalt je String-Variable in dieser Sprache (setstring)."""
        out = {}
        for sid, v in self.g.strings.items():
            refs = self.g.string_refs.get(sid)
            if not refs:
                raise v["line"].err(f"String '{sid}' wird nie gesetzt (setstring)")
            try:
                out[v["original"]] = (v["slot"], max(len(src.line(r)) for r in refs))
            except TextError as e:
                raise v["line"].err(str(e))
        return out

    def jump_unless(self, ln, cond, target):
        """Springt nach target, sobald eine Teilbedingung nicht erfüllt ist."""
        for kind, name, neg in cond:
            n = COND_NOT if neg else 0
            if kind == "var":
                var, cmp, value = name
                if not 0 <= value < 256:
                    raise ln.err("Variablen haben Werte 0…255")
                self.op("JUNLESSV", VAR_CMP[cmp] | n, self.var(ln, var), value, target)
            elif kind == "pos":
                actor, axis, cmp, value = name
                if self.actor(ln, actor) == NONE8:
                    raise ln.err("Positionsbedingung braucht einen Actor")
                k = (POS_Y if axis == "y" else 0) | (POS_GT if cmp == ">" else 0) | n
                self.op("JUNLESSPOS", k, self.actor(ln, actor), value, target)
            elif kind in ("has", "open", "hover"):
                k = {"has": COND_HAS, "open": COND_OPEN, "hover": COND_HOVER}[kind]
                self.op("JUNLESS", k | n, self.obj(ln, name), target)
            else:
                self.op("JUNLESS", COND_FLAG | n, self.flag(name), target)

    # ---- Skripte ----

    def op(self, name, *operands):
        self.b.put("u8", OP[name])
        types = dict(OPCODES)[name]
        if len(types) != len(operands):
            raise CompileError(f"interner Fehler: {name} erwartet {len(types)} Operanden")
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
        """repeat … end: Endlosschleife (Hintergrundabläufe); verlassen nur
        durch stop oder Raumwechsel."""
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
        """Obergrenze, wie viele Optionen gleichzeitig sichtbar sein können:
        jede Teilbedingung (ohne „not“) als freier Wahrheitswert, alle
        Belegungen durchprobiert. Abhängigkeiten zwischen Teilbedingungen
        (etwa zwei Vergleiche derselben Variablen) bleiben unberücksichtigt –
        das macht die Grenze höchstens größer, nie zu klein."""
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
        """Dialogauswahl als Code: CHOICES, je Option Bedingung + OPTION, ASK.
        Nach jeder Option geht es zurück zur Auswahl (Bedingungen neu
        geprüft), bis eine Option 'done' ausführt oder keine mehr übrig ist."""
        if self.in_routine:
            raise ln.err("'choose' ist in einem Hintergrundablauf nicht erlaubt")
        n = self.visible_bound(options)
        if n > MAX_OPTIONS:
            raise ln.err(f"bis zu {n} Optionen gleichzeitig sichtbar, höchstens {MAX_OPTIONS}")
        self.max_visible = max(self.max_visible, n)
        l_top, l_end = self.label("choose"), self.label("chosen")
        targets = [self.label("opt") for _ in options]
        self.b.mark(l_top)
        self.op("CHOICES")
        for opt, target in zip(options, targets):
            # Die Liste zeigt die erste Zeile, die gewählte Option erscheint ganz.
            try:
                lines = wrap(self.src.line(opt["text"]), OPTION_COLS, self.src.widths())
            except TextError as e:
                raise opt["line"].err(str(e))
            # Ist sie gewählt, schrumpft die Liste darunter bis auf eine Zeile.
            if len(lines) > SCREEN_ROWS - 1:
                raise opt["line"].err(f"Option {opt['text']} ({self.src.name}) braucht {len(lines)} Zeilen; "
                                      f"mit der Optionsliste passt das nicht auf das Display")
            skip = self.label("optskip")
            if opt["cond"]:
                self.jump_unless(opt["line"], opt["cond"], skip)
            self.text_max = max(self.text_max, self.shown_len("\n".join(lines)))
            self.op("OPTION", self.string("\n".join(lines)), target)
            self.b.mark(skip)
        self.op("ASK")
        # Sind alle Optionen ausgeblendet, läuft die Engine hier weiter.
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
            raise ln.err(f"'{kw}' ist in einem Hintergrundablauf nicht erlaubt")
        if kw == "say":
            need(2, "say <actor|narrator> <text>  (Verweis, z. B. r38.s203#1 oder r38.s203#1:2)")
            actor = self.actor(ln, a[0])
            for bubble in self.bubbles(ln, a[1]):
                self.text_max = max(self.text_max, self.shown_len(bubble))
                self.op("SAY", actor, self.string(bubble))
        elif kw == "costume":
            need(2, "costume <actor> <wie-actor>  (zurück: costume <actor> <actor>)")
            actor, look = self.actor(ln, a[0]), self.actor(ln, a[1])
            if NONE8 in (actor, look):
                raise ln.err("costume braucht zwei Actors")
            self.op("COSTUME", actor, look)
        elif kw == "pan":
            need(1, "pan <x>  (Raumkoordinate, Bildmitte)")
            try:
                x = int(a[0], 0)
            except ValueError:
                raise ln.err("pan: x muss eine Zahl sein")
            if not 0 <= x < 0x8000:
                raise ln.err("pan: 0 ≤ x < 32768")
            self.op("PAN", x)
        elif kw == "flash":
            need(1, "flash <frames>  (60 = 1 s)")
            n = int(a[0])
            if not 0 < n < 256:
                raise ln.err("flash: 1…255 Frames")
            self.op("FLASH", n)
        elif kw == "call":
            need(1, "call <sub>")
            if self.in_routine:
                raise ln.err("call ist in einem Hintergrundablauf nicht erlaubt (play startet eine Szene)")
            if a[0] not in self.g.subs:
                raise ln.err(f"Unterprogramm '{a[0]}' unbekannt")
            self.op("CALL", self.L(f"sub_{a[0]}"))
        elif kw == "play":
            need(1, "play <szene>")
            if not self.in_routine:
                raise ln.err("play nur in einem Hintergrundablauf (im Vordergrund die Befehle direkt schreiben)")
            if a[0] not in self.g.cutscenes:
                raise ln.err(f"Szene '{a[0]}' unbekannt")
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
            need(2, f"{kw} <variable> <zahl>")
            try:
                value = int(a[1], 0)
            except ValueError:
                raise ln.err(f"{kw}: Zahl erwartet")
            if not (0 <= value < 256 if kw == "let" else -128 <= value < 128):
                raise ln.err("let: 0…255, add: -128…127")
            self.op(kw.upper(), self.var(ln, a[0]), value & 0xFF)
        elif kw == "setstring":
            need(2, "setstring <string> <text>")
            sid = self.string_var(ln, a[0])
            self.op("SETSTR", self.g.strings[sid]["slot"], self.text_line(ln, a[1]))
        elif kw == "setchar":
            need(3, "setchar <string> <stelle> <zeichencode>")
            sid = self.string_var(ln, a[0])
            try:
                pos = int(a[1], 0)
            except ValueError:
                raise ln.err("setchar: Stelle muss eine Zahl sein")
            if not 0 <= pos < self.string_size - 1:
                raise ln.err(f"setchar: Stelle {pos} liegt außerhalb des Strings")
            if re.fullmatch(r"\d+|0x[0-9a-fA-F]+", a[2]):
                ch = int(a[2], 0)
                if not 0 < ch < 256:
                    raise ln.err("setchar: Zeichencode 1…255")
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
                raise ln.err(f"{kw}: Koordinaten müssen Zahlen sein")
            self.op(kw.upper(), self.actor(ln, a[0]), x, y)
        elif kw == "face":
            need(2, "face <actor> left|right|front")
            if a[1] not in DIRS:
                raise ln.err("Richtung: left|right|front")
            self.op("FACE", self.actor(ln, a[0]), DIRS[a[1]])
        elif kw in ("set", "clear"):
            need(1, f"{kw} <flag>")
            self.op(kw.upper(), self.flag(a[0]))
        elif kw in ("pickup", "lose", "hide", "show"):
            need(1, f"{kw} <objekt>")
            self.op(kw.upper(), self.obj(ln, a[0]))
        elif kw == "state":
            need(2, "state <objekt> <n>")
            self.op("STATE", self.obj(ln, a[0]), int(a[1]))
        elif kw == "music":
            need(1, "music <id>|stop")
            if a[0] == "stop":
                self.op("MUSIC", NONE8)
            elif a[0] in self.g.music:
                self.op("MUSIC", self.g.music[a[0]]["index"])
            else:
                raise ln.err(f"Musik '{a[0]}' unbekannt")
        elif kw == "wait":
            if a[:1] == ["random"]:
                need(3, "wait random <min> <max>  (Frames, 60 = 1 s)")
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
            need(1, "start <ablauf>")
            if a[0] not in self.g.routines:
                raise ln.err(f"Ablauf '{a[0]}' unbekannt")
            self.op("START", self.L(f"routine_{a[0]}"))
        elif kw == "stop":
            need(1, "stop <ablauf>")
            if a[0] not in self.g.routines:
                raise ln.err(f"Ablauf '{a[0]}' unbekannt")
            self.op("STOP", self.L(f"routine_{a[0]}"))
        elif kw == "room":
            # room <raum> [at x y] [face dir]
            if not a or a[0] not in self.g.rooms:
                raise ln.err(f"Raum '{a[0] if a else ''}' unbekannt (Syntax: room <raum> [at x y] [face dir])")
            if self.in_entry:
                raise ln.err("Entry-Skripte dürfen keinen Raum wechseln")
            rest = a[1:]
            x, y, face = 0xFFFF, 0, DIRS["front"]
            if rest[:1] == ["at"]:
                if len(rest) < 3:
                    raise ln.err("room … at x y")
                try:
                    x, y = int(rest[1], 0), int(rest[2], 0)
                except ValueError:
                    raise ln.err("room … at: Koordinaten müssen Zahlen sein")
                rest = rest[3:]
            if rest[:1] == ["face"]:
                if len(rest) < 2 or rest[1] not in DIRS:
                    raise ln.err("room … face left|right|front")
                face = DIRS[rest[1]]
                rest = rest[2:]
            if rest:
                raise ln.err(f"room: unerwartet {rest}")
            self.op("ROOM", self.g.rooms[a[0]]["index"], x, y, face)
        elif kw == "card":
            need(1, "card <id>")
            if a[0] not in self.g.cards:
                raise ln.err(f"Karte '{a[0]}' unbekannt")
            c = self.g.cards[a[0]]
            music = NONE8
            if c["music"]:
                if c["music"] not in self.g.music:
                    raise c["line"].err(f"Musik '{c['music']}' unbekannt")
                music = self.g.music[c["music"]]["index"]
            self.op("CARD", self.card_image(a[0]), music)
        elif kw == "done":
            need(0, "done")
            if not getattr(self, "choose_end", None):
                raise ln.err("'done' nur innerhalb einer choose-Option")
            self.op("JMP", self.choose_end[-1])
        else:
            raise ln.err(f"unbekannter Befehl '{kw}'")

    # ---- Gesamtlayout ----
    #
    # game.bin:  LangDir + LangEntry je Sprache
    #            gemeinsam: Figuren, Laufwege, Platzierungen, Musik
    #            je Sprache: GameHeader, Verben, Objekte, Räume, Skripte, Texte
    #            gemeinsam: Bilder, Sprachnamen

    def compile(self):
        g, b = self.g, self.b
        self.check()
        if not g.languages:
            raise CompileError("keine Sprachfassung angegeben (advc.py --original <Kopie> …)")
        if len(g.objects) > 254 or len(g.actors) > 254 or len(g.rooms) > 254:
            raise CompileError("höchstens 254 Objekte/Actors/Räume")

        b.mark("langdir")
        b.raw(bytes(record_size("LangDir") + len(g.languages) * record_size("LangEntry")))

        for name in g.numbers:
            self.var(None, name)
        for src in g.languages:
            src.strings = self.string_widths(src)
            src.numbers = {orig: g.vars[name] for name, orig in g.numbers.items()}
        self.string_size = 1 + max([w for src in g.languages for _, w in src.strings.values()], default=0)
        if self.string_size > 64:
            raise CompileError("String-Variablen höchstens 63 Zeichen")

        self.call_stack = self.call_depth()
        self.compile_shared()
        for src in g.languages:
            self.src = src
            self.strings = {}
            self.compile_language()

        # Bilder
        cursor = self.image(g.cursor, None, mask=True) if g.cursor else None
        title = self.image(g.title, None) if g.title else None
        self.cursor_label = cursor["label"] if cursor else NONE24
        self.title_label = title["label"] if title else NONE24
        for img in self.images.values():
            b.mark(img["label"])
            b.raw(img["data"])
        for src in g.languages:
            b.mark(f"langname:{src.code}")
            b.raw(src.name.encode("cp437") + b"\0")

        # Header erst jetzt (Cursor und Titel sind jetzt abgelegt) in ihre
        # reservierten Plätze.
        b.resolve()
        for src in g.languages:
            self.src = src
            head = self.header_record()
            head.resolve()
            at = b.labels[self.L("header")]
            b.data[at:at + len(head.data)] = head.data

        # Der Bereich des Sprachverzeichnisses ist hier noch genullt; die
        # Build-ID hängt also nur vom Inhalt ab.
        self.build_id = int.from_bytes(hashlib.sha1(bytes(b.data)).digest()[:2], "little")
        head = Blob()
        head.labels = b.labels
        head.record("LangDir", magic=0x464D, buildId=self.build_id, count=len(g.languages))
        for src in g.languages:
            head.record("LangEntry", name=f"langname:{src.code}", header=f"{src.code}:header")
        head.resolve()
        b.data[0:len(head.data)] = head.data
        if len(b.data) >= 1 << 24:
            raise CompileError("Daten größer als 16 MB")
        return bytes(b.data)

    def compile_shared(self):
        """Teile ohne Text: Figuren, Laufwege und Platzierungen, Musik."""
        g, b = self.g, self.b
        b.mark("actors")
        for aid, a in g.actors.items():
            img = self.image(a["sprite"], a["line"], mask=True)
            first, count = a["walk"]
            for f in (a["stand"], a["talk"], a["front"], a["fronttalk"], first, first + count - 1):
                if not 0 <= f < img["frames"]:
                    raise a["line"].err(f"Frame {f} existiert nicht (Sprite hat {img['frames']})")
            obj = self.obj(a["line"], a["object"]) if a.get("object") else NONE8
            b.record("ActorRec", sprite=img["label"],
                     stand=a["stand"], walkFirst=first, walkCount=count, talk=a["talk"],
                     front=a["front"], frontTalk=a["fronttalk"], object=obj,
                     depth=f"depth_{aid}" if a.get("levels") else NONE24)
        for aid, a in g.actors.items():
            if not a.get("levels"):
                continue
            # Absteigend: eine Stufe gilt, sobald die Größe unter der Mitte zwischen
            # ihrem und dem nächstgrößeren Anteil liegt (u8 Schwelle, u24 Sprite).
            b.mark(f"depth_{aid}")
            b.put("u8", len(a["levels"]))
            prev = 1.0
            for f, sprite in a["levels"]:
                img = self.image(sprite, a["line"], mask=True)
                b.put("u8", round((f + prev) / 2 * 255))
                b.put("u24", img["label"])
                prev = f

        for rid, r in g.rooms.items():
            bg = self.image(r["bg"], r["line"])
            if not 64 <= bg["h"] <= 255 or bg["w"] < 128:
                raise r["line"].err("Raumbild muss 64–255 px hoch und mindestens 128 px breit sein")
            r["width"] = bg["w"]
            r["height"] = bg["h"]
            r["bg_label"] = bg["label"]
            if not r["boxes"] and not r.get("blank"):
                raise r["line"].err("Raum ohne Laufflächen (walkbox / walkboxes original)")
            if len(r["boxes"]) > 254:
                raise r["line"].err("höchstens 254 Laufflächen pro Raum (NONE8 markiert „kein Weg“)")
            b.mark(f"boxes_{rid}")
            for corners, ln, (top, bottom) in r["boxes"]:
                pts = [(int(round(x)), int(round(y))) for x, y in corners]
                # Laufwege dürfen über den Bildrand hinausgehen: Treppen nach
                # unten, Ausgänge seitlich aus dem Bild (wie im Original).
                if not all(0 <= x < 0x8000 and 0 <= y < 256 for x, y in pts):
                    raise ln.err(f"Lauffläche {pts} außerhalb des Wertebereichs")
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
                        raise ln.err(f"frames {p['frames']} teilt die {img['frames']} Bilder nicht")
                    image = img["label"]
                else:
                    if p["w"] is None:
                        raise ln.err("Hotspot ohne Bild braucht w h")
                    w, h, image = p["w"], p["h"], NONE24
                b.record("PlaceRec", object=oi, x=p["x"], y=p["y"], w=w, h=h,
                         walkX=p["walk"][0], walkY=p["walk"][1], face=p["face"],
                         image=image, frames=p["frames"], speed=p["speed"])

        b.mark("music")
        for mid in g.music:
            b.put("u24", f"track_{mid}")
        for mid, m in g.music.items():
            b.mark(f"track_{mid}")
            b.record("TrackRec", count=len(m["notes"]), loop=int(m["loop"]))
            for ocr, ms in m["notes"]:
                b.record("NoteRec", ocr=ocr, ms=ms)

    def compile_language(self):
        """Alles mit Text für die Sprache self.src: Verben, Objekte, Räume
        (wegen der Entry-Skripte), Skripte, Texte. Der Header folgt später."""
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
                    raise h["line"].err(f"Verb '{h['verb']}' unbekannt")
                if h["verb"] == "other" and h["other"]:
                    raise h["line"].err("'on other' gilt für alle übrigen Verben, ohne zweites Objekt")
                other = self.obj(h["line"], h["other"]) if h["other"] else NONE8
                key = (h["verb"], other)
                if key in seen:
                    raise h["line"].err("Handler doppelt")
                seen.add(key)
                verb = VERB_ANY if h["verb"] == "other" else g.verbs[h["verb"]]["index"]
                b.record("VerbEntry", verb=verb, other=other,
                         script=L(f"objscript_{oid}_{i}"))
            b.put("u8", NONE8)

        b.mark(L("rooms"))
        for rid, r in g.rooms.items():
            b.record("RoomRec", background=r["bg_label"], width=r["width"], height=r["height"],
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
        if isinstance(start, tuple):  # Kurzform: start <raum>
            start_room, start_ln = start
            if start_room not in g.rooms:
                raise start_ln.err(f"Raum '{start_room}' unbekannt")
            b.mark(L("start"))
            self.op("ROOM", g.rooms[start_room]["index"], 0xFFFF, 0, 0)
            self.op("END")
        else:
            self.script(start, L("start"))

        ui = UI_TEXT.get(self.src.code, UI_TEXT["en"])
        self.lang_ui[self.src.code] = {key: self.string(text) for key, text in ui.items()}

        # Texte zuletzt: sie entstehen während der Codegenerierung
        for text, label in self.strings.items():
            b.mark(label)
            b.raw(self.encode_text(text) + b"\0")

    def header_record(self):
        """GameHeader der Sprache self.src (Labels über das Haupt-Blob)."""
        g, L = self.g, self.L
        title_music = NONE8
        if g.title_music:
            name, ln = g.title_music
            if name not in g.music:
                raise ln.err(f"Musik '{name}' unbekannt")
            title_music = g.music[name]["index"]
        inventory_verb = NONE8
        if g.inventory_verb:
            name, ln = g.inventory_verb
            if name not in g.verbs:
                raise ln.err(f"Verb '{name}' unbekannt")
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
                    titleMusic=title_music, inventoryVerb=inventory_verb,
                    uiSoundOn=ui["sound_on"], uiSoundOff=ui["sound_off"], uiEmpty=ui["empty"])
        return head

    def check(self):
        g = self.g
        for req, what in ((g.verbs, "verb"), (g.actors, "actor"), (g.rooms, "room")):
            if not req:
                raise CompileError(f"mindestens ein '{what}' nötig")
        if g.start_room is None:
            raise CompileError("'start <raum>' fehlt")
        if "walk" not in g.verbs or g.verbs["walk"]["index"] != 0:
            raise CompileError("das erste Verb muss 'walk' sein (Standardverb der Engine)")

    # ---- Header für die Engine ----

    def header(self):
        g = self.g
        out = [
            "// Generiert von tools/advc.py – nicht von Hand ändern.",
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
            f"constexpr uint8_t MAX_OPTIONS = {self.max_visible};  // gleichzeitig sichtbare Optionen",
            f"constexpr uint8_t CHOICE_ROWS = {CHOICE_ROWS};",
            f"constexpr uint8_t TEXT_COLS = {TEXT_COLS};",
            f"constexpr uint8_t TEXT_ROWS = {TEXT_ROWS};",
            f"constexpr uint8_t OPTION_COLS = {OPTION_COLS};",
            f"constexpr uint8_t STRING_SLOTS = {max(1, len(g.strings))};",
            f"constexpr uint8_t CALL_DEPTH = {self.call_stack};  // Rücksprünge (call, Entry-Skripte)",
            f"constexpr uint8_t STRING_SIZE = {self.string_size};",
            f"constexpr uint8_t TEXT_BUFFER = {self.text_buffer()};  // längste Sprechblase/Option + NUL",
            "",
            "constexpr uint8_t COND_FLAG = 0x00, COND_HAS = 0x01, COND_OPEN = 0x02, COND_HOVER = 0x03, COND_NOT = 0x80;",
            "constexpr uint8_t POS_Y = 0x01, POS_GT = 0x02;",
            "constexpr uint8_t VAR_EQ = 0x00, VAR_LT = 0x01, VAR_GT = 0x02;",
            "constexpr uint8_t VERB_ANY = 0xFE;  // VerbEntry.verb: „on other“",
            "constexpr uint16_t WALK_DIRECT = 0xFFFF;  // PlaceRec.walkX: Verbskript startet ohne Hinlaufen",
            "",
            "enum Op : uint8_t {",
        ]
        out += [f"  OP_{name} = {i}," for i, (name, _) in enumerate(OPCODES)]
        out += ["};", ""]
        out.append("// Befehlslängen in Bytes inkl. Opcode (CHOOSE: ohne die Options-Records)")
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
            out.append(f'static_assert(sizeof({name}) == {record_size(name)}, "{name}: Layout passt nicht zu advc.py");')
            out.append("")
        return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Pocket Adventure FX: Spielbeschreibung → FX-Daten")
    ap.add_argument("source", type=Path, help="Spielbeschreibung (.adv)")
    ap.add_argument("--bin", type=Path, required=True, help="Ausgabe: FX-Datenblock")
    ap.add_argument("--header", type=Path, required=True, help="Ausgabe: gamedata.h")
    ap.add_argument("--original", type=Path, action="append", default=[],
                    help="Verzeichnis einer Originalkopie (DISK01.LEC …); mehrfach für mehrere Sprachen. "
                         "Grafiken, Laufwege und Musik kommen aus der ersten, die Texte aus jeder.")
    ap.add_argument("--preview", type=Path, help="aus Originaldaten erzeugte Bilder als PNG hierhin")
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
                raise CompileError(f"{d}: Sprache {src.name} ist schon angegeben")
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
                img["source"][2].save(args.preview / f"{img['source'][1]}.png")
    music = sum(len(m["notes"]) for m in game.music.values())
    langs = ", ".join(src.name for src in game.languages)
    print(f"advc: {len(data)} Bytes, Sprachen: {langs}, {len(game.rooms)} Raum/Räume, {len(game.objects)} Objekte, "
          f"{len(game.flags)} Flags, {music} Noten, Build 0x{comp.build_id:04X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
