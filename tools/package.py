#!/usr/bin/env python3
"""Packs sketch and FX data into an .arduboy package.

Format (schema 4, as read by Arduboy Toolset and cart editors): a ZIP
with info.json, the sketch as Intel HEX, the FX data and a cart image
(128×64, here the title image). The cart builder itself patches the FX data
page into the program; the engine reads it via FX::begin().

The package is built from your own copy of the game and contains its graphics
and texts – it is for personal use only, not for redistribution.
"""
import argparse
import datetime
import json
import sys
import zipfile
from pathlib import Path

from PIL import Image

TITLE = "Pocket Adventure FX"


def languages(game_bin):
    """Language names from the language directory at the start of game.bin
    (advc.py: LangDir u16 magic, u16 build, u8 count; per LangEntry u24 name,
    u24 header)."""
    data = game_bin.read_bytes()
    if int.from_bytes(data[0:2], "little") != 0x464D:
        raise ValueError(f"{game_bin}: not a game data block from advc.py")
    names = []
    for i in range(data[4]):
        at = int.from_bytes(data[5 + 6 * i:8 + 6 * i], "little")
        names.append(data[at:data.index(0, at)].decode("cp437"))
    return ", ".join(names)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hex", type=Path, required=True)
    ap.add_argument("--data", type=Path, required=True, help="FX data (fxdata-data.bin)")
    ap.add_argument("--cart", type=Path, required=True, help="cart image 128×64 (title screen)")
    ap.add_argument("--game", type=Path, required=True, help="game.bin (for the language list)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    langs = languages(args.game)
    cart = Image.open(args.cart).convert("1")
    if cart.size != (128, 64):
        print(f"{args.cart}: cart image must be 128×64, not {cart.size}", file=sys.stderr)
        return 1

    info = {
        "schemaVersion": 4,
        "title": TITLE,
        "description": "Unofficial demake of The Secret of Monkey Island for the Arduboy FX, "
                       f"built from your own copy of the original game. Languages: {langs}.",
        "version": "1.0",
        "date": datetime.date.today().isoformat(),
        "genre": "Adventure",
        "binaries": [{
            "title": TITLE,
            "filename": "PocketAdventureFX.hex",
            "flashdata": "PocketAdventureFX-data.bin",
            "device": "ArduboyFX",
            "cartimage": "cart.png",
        }],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.json", json.dumps(info, indent=2, ensure_ascii=False))
        z.write(args.hex, "PocketAdventureFX.hex")
        z.write(args.data, "PocketAdventureFX-data.bin")
        with z.open("cart.png", "w") as f:
            cart.save(f, "PNG")
    print(f"package: {args.out} ({args.out.stat().st_size} bytes, {langs})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
