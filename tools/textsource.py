"""Spieltexte aus einer Originalkopie, aufbereitet für das 128×64-Display.

Die Spielbeschreibung (game.adv) enthält keine Texte, nur Verweise wie
r38.s203#1 (siehe scumm_text.py). Pro Sprachfassung, die beim Bauen
angegeben wird, löst eine TextSource die Verweise auf: Sonderzeichen des
Originalfonts umsetzen, umbrechen, auf Sprechblasen verteilen.

Verweis-Syntax:  <bezeichner>[:seite]
  r38.s203#1     der ganze Text (alle Sprechblasen, die das Original hat)
  r38.s203#1:2   nur die zweite Sprechblase des Originals
"""
import re
import textwrap
from pathlib import Path

import scumm_text
from scumm_v4 import ScummError

REF = re.compile(r"(?:s\d+|r\d+\.(?:s\d+|en|ex)|o\d+)(?:#\d+|\.name)(?::\d+)?")

# Sprache einer Kopie am ersten Verb des Verbskripts (s22#1, „Open“).
LANGUAGES = {
    "Open": ("en", "English"),
    "Öffne": ("de", "Deutsch"),
    "Ouvrir": ("fr", "Français"),
    "Apri": ("it", "Italiano"),
    "Abrir": ("es", "Español"),
}

# Zeichen des Originalfonts ohne Entsprechung im Arduboy-Font (CP437):
# ^ ist dort eine Auslassung („Er^“ = „Er…“), ` ein Anführungszeichen,
# @ füllt Objektnamen auf, 0x0F ist ein Zierzeichen hinter einem Inselnamen.
_FONT = {"^": "...", "`": '"', "@": "", "\x0f": "", "☼": ""}


class TextError(Exception):
    pass


def detect_language(texts, game_dir):
    """(Kürzel, Name) einer Kopie, z. B. ("de", "Deutsch"); texts aus
    scumm_text.read_texts."""
    first = texts.get("s22#1")
    word = first.plain() if first else None
    if word not in LANGUAGES:
        raise TextError(f"{game_dir}: Sprachfassung nicht erkannt (erstes Verb {word!r})")
    return LANGUAGES[word]


def to_font(text):
    out = "".join(_FONT.get(c, c) for c in text)
    try:
        # Platzzeichen von Variablen (scumm_text.SLOT_BASE) sind kein Text
        "".join(c for c in out if not 0xE000 <= ord(c) <= 0xF8FF).encode("cp437")
    except UnicodeEncodeError as e:
        raise TextError(f"Zeichen nicht im Arduboy-Font (CP437): {out!r}") from e
    return out


# Platzhalter einer Variablen (scumm_text.STRING_VAR/INT_VAR + Platzzeichen):
# Beim Umbruch zählt er so breit wie der längste mögliche Inhalt.
_FILL = "\uf8ff"   # Privatbereich: kommt in keinem Text vor


def wrap(text, cols, widths=None):
    """Umbruch auf cols Spalten; '\\n' im Original erzwingt einen Umbruch.
    widths: Platzhalterzeichen → Breite seines längsten Inhalts."""
    for code, width in (widths or {}).items():
        text = text.replace(code, code + _FILL * (width - 2))
    lines = []
    for part in text.split("\n"):
        lines += textwrap.wrap(" ".join(part.split()), cols) or []
    return [l.replace(_FILL, "") for l in lines]


class TextSource:
    def __init__(self, game_dir):
        self.dir = Path(game_dir)
        try:
            self.texts = scumm_text.read_texts(game_dir)
        except ScummError as e:
            raise TextError(f"{game_dir}: {e}") from e
        self.code, self.name = detect_language(self.texts, game_dir)
        # String-Variablen des Originals, die die Engine kennt:
        # Nummer im Original → (Platz, längster Inhalt in Zeichen)
        self.strings = {}
        # Zahlen (Code 4): Nummer im Original → Variable der Engine
        self.numbers = {}

    # Zahlen haben höchstens drei Stellen (0…255).
    NUMBER_WIDTH = 3

    def widths(self):
        """Platzhalter (Kennbyte + Platz) → Breite des Inhalts (für wrap)."""
        base = scumm_text.SLOT_BASE
        out = {scumm_text.STRING_VAR + chr(base + slot): width for slot, width in self.strings.values()}
        out.update({scumm_text.INT_VAR + chr(base + var): self.NUMBER_WIDTH for var in self.numbers.values()})
        return out

    def _plain(self, ref):
        if not REF.fullmatch(ref):
            raise TextError(f"kein Textverweis: {ref!r} (z. B. r38.s203#1, o498.name)")
        ident, _, page = ref.partition(":")
        text = self.texts.get(ident)
        if text is None:
            raise TextError(f"{ref}: gibt es in der Fassung {self.name} nicht")
        try:
            plain = text.plain({var: slot for var, (slot, _) in self.strings.items()}, self.numbers)
        except ScummError as e:
            raise TextError(f"{ref}: {e}") from e
        pages = [to_font(p).strip() for p in plain.split("\f")]
        if page:
            n = int(page)
            if not 1 <= n <= len(pages):
                raise TextError(f"{ref}: Seite {n} gibt es nicht (der Text hat {len(pages)})")
            pages = [pages[n - 1]]
        pages = [p for p in pages if p]
        if not pages:
            raise TextError(f"{ref}: der Text ist leer")
        return pages

    def bubbles(self, ref, cols, rows):
        """Sprechblasen (je höchstens rows Zeilen à cols Zeichen) für einen Text."""
        out = []
        for page in self._plain(ref):
            lines = wrap(page, cols, self.widths())
            if any(len(l) > cols for l in lines):
                raise TextError(f"{ref}: Wort länger als {cols} Zeichen")
            out += ["\n".join(lines[i:i + rows]) for i in range(0, len(lines), rows)]
        return out

    def line(self, ref):
        """Einzeiliger Text (Name, Verb): Seiten mit Leerzeichen verbunden."""
        return " ".join(" ".join(p.split()) for p in self._plain(ref))
