#!/usr/bin/env python3
"""Erzeugt die Platzhalter-Sprites unter game/art/ als PNG.

Kulissen, Titel und Figuren kommen aus den Originaldaten (tools/scumm_v4.py);
übrig ist ein eigenes Kleinteil ohne Original-Gegenstück: der Cursor. Die PNGs sind die
Quellen für den Asset-Compiler (tools/advc.py); handgepixelte oder aus dem
Original konvertierte Sprites können sie jederzeit ersetzen. Das Skript
überschreibt vorhandene Dateien nur mit --force.

Farbkonvention (wie fxdata-build.py): weiß = Pixel an, schwarz = Pixel aus,
Alpha < 255 = transparent (erzeugt eine Maske).

ASCII-Sprites: '#' weiß, 'o' schwarz (deckend), ' ' transparent. outline
legt einen deckenden schwarzen Rand um jede Figur, damit sie sich von der
gerasterten Kulisse abhebt.
"""
import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "game" / "art"

WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
CLEAR = (0, 0, 0, 0)


# --------------------------------------------------------------------------
# ASCII-Sprites
# --------------------------------------------------------------------------

def ascii_frames(frames, outline):
    """Wandelt gleich große ASCII-Frames in einen horizontalen Sprite-Streifen.

    outline = Breite des deckenden schwarzen Randes in Pixeln (0 = keiner);
    jeder Frame wächst um diese Breite pro Seite. Vor den gerasterten
    Original-Kulissen braucht es 2 px, damit Figuren nicht im Muster aufgehen.
    """
    h = len(frames[0])
    w = len(frames[0][0])
    for f in frames:
        if len(f) != h or any(len(row) != w for row in f):
            raise ValueError("ASCII-Frames müssen gleich groß sein")
    pad = outline
    fw, fh = w + 2 * pad, h + 2 * pad
    img = Image.new("RGBA", (fw * len(frames), fh), CLEAR)
    px = img.load()
    for i, f in enumerate(frames):
        ox = i * fw + pad
        for y, row in enumerate(f):
            for x, c in enumerate(row):
                if c == "#":
                    px[ox + x, pad + y] = WHITE
                elif c == "o":
                    px[ox + x, pad + y] = BLACK
        # Rand schrittweise wachsen lassen: erst um die weißen Pixel, dann
        # um alles Deckende.
        for step in range(outline):
            grow = []
            for y in range(fh):
                for x in range(i * fw, (i + 1) * fw):
                    if px[x, y][3]:
                        continue
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if i * fw <= nx < (i + 1) * fw and 0 <= ny < fh and \
                                (px[nx, ny] == WHITE if step == 0 else px[nx, ny][3]):
                            grow.append((x, y))
                            break
            for x, y in grow:
                px[x, y] = BLACK
    return img, fw, fh



CURSOR = [[
    "   #   ",
    "   #   ",
    "       ",
    "## # ##",
    "       ",
    "   #   ",
    "   #   ",
]]


# --------------------------------------------------------------------------

def save(img, rel, force):
    path = ART / rel
    if path.exists() and not force:
        print(f"  übersprungen (existiert): {rel}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    print(f"  geschrieben: {rel}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="vorhandene PNGs überschreiben")
    args = ap.parse_args()

    img, fw, fh = ascii_frames(CURSOR, outline=1)
    save(img, "ui/cursor.png", args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
