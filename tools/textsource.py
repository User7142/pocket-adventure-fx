"""Game texts from a copy of the game, prepared for the 128×64 display.

The game description (game.adv) contains no texts, only references such as
r38.s203#1 (see scumm_text.py). For each language version given at build
time, a TextSource resolves the references: converting special characters of
the original font, wrapping, distributing across speech bubbles.

Reference syntax:  <identifier>[:page]
  r38.s203#1     the whole text (all speech bubbles the original has)
  r38.s203#1:2   only the second speech bubble of the original
"""
import re
import textwrap
from pathlib import Path

import scumm_text
from scumm_v4 import ScummError

REF = re.compile(r"(?:s\d+|r\d+\.(?:s\d+|en|ex)|o\d+)(?:#\d+|\.name)(?::\d+)?")

# Language of a copy, from the first verb of the verb script (s22#1, “Open”).
LANGUAGES = {
    "Open": ("en", "English"),
    "Öffne": ("de", "Deutsch"),
    "Ouvrir": ("fr", "Français"),
    "Apri": ("it", "Italiano"),
    "Abrir": ("es", "Español"),
}

# Characters of the original font without a counterpart in the Arduboy font (CP437):
# there ^ is an ellipsis („Er^“ = „Er…“), ` a quotation mark,
# @ pads object names, 0x0F is an ornament after an island name.
_FONT = {"^": "...", "`": '"', "@": "", "\x0f": "", "☼": ""}


class TextError(Exception):
    pass


def detect_language(texts, game_dir):
    """(code, name) of a copy, e.g. ("de", "Deutsch"); texts from
    scumm_text.read_texts."""
    first = texts.get("s22#1")
    word = first.plain() if first else None
    if word not in LANGUAGES:
        raise TextError(f"{game_dir}: language version not recognised (first verb {word!r})")
    return LANGUAGES[word]


def to_font(text):
    out = "".join(_FONT.get(c, c) for c in text)
    try:
        # Slot characters of variables (scumm_text.SLOT_BASE) are not text
        "".join(c for c in out if not 0xE000 <= ord(c) <= 0xF8FF).encode("cp437")
    except UnicodeEncodeError as e:
        raise TextError(f"character not in the Arduboy font (CP437): {out!r}") from e
    return out


# Placeholder of a variable (scumm_text.STRING_VAR/INT_VAR + slot character):
# when wrapping, it counts as wide as its longest possible content.
_FILL = "\uf8ff"   # private use area: never occurs in any text


def wrap(text, cols, widths=None):
    """Wrap to cols columns; '\\n' in the original forces a line break.
    widths: placeholder character → width of its longest content."""
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
        # String variables of the original that the engine knows:
        # number in the original → (slot, longest content in characters)
        self.strings = {}
        # Numbers (code 4): number in the original → engine variable
        self.numbers = {}

    # Numbers have at most three digits (0…255).
    NUMBER_WIDTH = 3

    def widths(self):
        """Placeholder (tag byte + slot) → width of the content (for wrap)."""
        base = scumm_text.SLOT_BASE
        out = {scumm_text.STRING_VAR + chr(base + slot): width for slot, width in self.strings.values()}
        out.update({scumm_text.INT_VAR + chr(base + var): self.NUMBER_WIDTH for var in self.numbers.values()})
        return out

    def _plain(self, ref):
        if not REF.fullmatch(ref):
            raise TextError(f"not a text reference: {ref!r} (e.g. r38.s203#1, o498.name)")
        ident, _, page = ref.partition(":")
        text = self.texts.get(ident)
        if text is None:
            raise TextError(f"{ref}: does not exist in the {self.name} language version")
        try:
            plain = text.plain({var: slot for var, (slot, _) in self.strings.items()}, self.numbers)
        except ScummError as e:
            raise TextError(f"{ref}: {e}") from e
        pages = [to_font(p).strip() for p in plain.split("\f")]
        if page:
            n = int(page)
            if not 1 <= n <= len(pages):
                raise TextError(f"{ref}: page {n} does not exist (the text has {len(pages)})")
            pages = [pages[n - 1]]
        pages = [p for p in pages if p]
        if not pages:
            raise TextError(f"{ref}: the text is empty")
        return pages

    def bubbles(self, ref, cols, rows):
        """Speech bubbles (each at most rows lines of cols characters) for a text."""
        out = []
        for page in self._plain(ref):
            lines = wrap(page, cols, self.widths())
            if any(len(l) > cols for l in lines):
                raise TextError(f"{ref}: word longer than {cols} characters")
            out += ["\n".join(lines[i:i + rows]) for i in range(0, len(lines), rows)]
        return out

    def line(self, ref):
        """Single-line text (name, verb): pages joined with spaces."""
        return " ".join(" ".join(p.split()) for p in self._plain(ref))
