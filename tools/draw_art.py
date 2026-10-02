#!/usr/bin/env python3
"""Generates the placeholder sprites under game/art/ as PNG.

Backgrounds, title and characters come from the original data (tools/scumm_v4.py);
what remains is one small custom piece without an original counterpart: the cursor. The PNGs
are the sources for the asset compiler (tools/advc.py); hand-pixelled sprites or sprites
converted from the original can replace them at any time. The script
overwrites existing files only with --force.

Colour convention (as in fxdata-build.py): white = pixel on, black = pixel off,
alpha < 255 = transparent (creates a mask).

ASCII sprites: '#' white, 'o' black (opaque), ' ' transparent. outline
puts an opaque black outline around each character so that it stands out
against the dithered background.
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
# ASCII sprites
# --------------------------------------------------------------------------

def ascii_frames(frames, outline):
    """Converts equally sized ASCII frames into a horizontal sprite strip.

    outline = width of the opaque black outline in pixels (0 = none);
    each frame grows by this width per side. Against the dithered original
    backgrounds it takes 2 px so that characters don't dissolve into the pattern.
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
        # Grow the outline step by step: first around the white pixels, then
        # around everything opaque.
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
