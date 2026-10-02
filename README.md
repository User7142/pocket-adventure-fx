# Pocket Adventure FX

A small point-and-click adventure engine for the **Arduboy FX** – plus a scene
description that recreates the first scenes of *The Secret of Monkey Island*
(Part One, Mêlée Island) from **your own copy** of the game.

The repository contains **nothing from the game**: no graphics, no texts, no music.
The scene description `game/game.adv` only refers to rooms, objects, costumes, sounds
and texts of the original (for example `r38.s203#1`, “first text of script 203 in
room 38”). When you build, a compiler reads your copy of the VGA floppy version and
writes everything into one package:

```bash
./build.sh ~/Games/MONKEY ~/Games/MONKEY-DE
```

The result is `dist/PocketAdventureFX.arduboy` for the Arduboy Toolset, a cart editor
or the Ardens emulator. Every copy you pass becomes a language in the game (tested:
English and German). The package contains material from your copy and is for your own
use only.

## What is included

- the intro with the title melody and the conversation with the lookout
- the chapter card “Part One” with its music
- the dock, the SCUMM Bar with the pirate leaders’ dialog, the kitchen and the cook
  (who leaves the kitchen every 30–50 seconds, as in the original)
- the ghost ship scene when you leave the bar for the first time
- the street with the pirates, their rat and parrot, the map seller and the doors
- the Voodoo Lady
- the map of the island: you can walk around; the village and the lookout can be entered

Exits to places that are not part of this cut say “Not included in this version”.
Dialogs, conditions and timings follow the original scripts.

## Controls

| Button | In the game | In the menu | During text/dialogs |
|---|---|---|---|
| D-pad | move the cursor (accelerates when held) | move the selection | choose an option (up/down) |
| A | execute the sentence (“Walk to” or the selected verb) | select verb or inventory item | skip text / confirm option |
| B | open the menu (verbs, inventory) | close the menu | – |

At start you choose the language; the game remembers it in EEPROM. On the title
screen B switches the sound on and off.

## Your game files

You need the **VGA floppy version** (not the CD or Special Edition): a directory that
directly contains `000.LFL` and `DISK01.LEC` … `DISK04.LEC`.

From the original floppies the files are packed in `.ZLH` archives. Without a DOS PC,
[7-Zip](https://www.7-zip.org/) extracts them (tip from spinal).

## Building on macOS and Linux

Requirements: Python 3.10+, curl, make.

```bash
./build.sh <copy> [<another copy> …]
```

On the first run the script downloads arduino-cli and the cores `arduino:avr@1.8.8`
and `arduboy-homemade:avr@1.4.1` into `build/arduino/`; Python packages go into
`.venv/`. Nothing is installed system-wide.

Graphics, walk boxes and music come from the first copy, the texts from each copy.
Warnings about `noreturn`, “Low memory” and clock skew are expected. At the end the
script prints `Done: dist/PocketAdventureFX.arduboy`.

## Building on Windows (WSL)

`build.sh` is a shell script, so on Windows it runs in WSL (the Windows Subsystem for
Linux); flashing then works with a Windows tool. Based on the guide by spinal.

1. **Install WSL once.** In PowerShell as administrator:
   ```powershell
   wsl --install -d Ubuntu
   ```
   Restart if asked, open “Ubuntu” from the Start menu and create a user.
2. **Install the tools** in the Ubuntu window (`apt update` first, otherwise the
   install can fail with 404 errors):
   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv curl make git
   ```
3. **Get the project.** Clone it, or download the ZIP from GitHub and extract it, for
   example to `F:\arduboy\pocket-adventure-fx`, with your game files next to it in
   `F:\arduboy\monkey`. Drive `F:` is `/mnt/f` in WSL:
   ```bash
   cd /mnt/f/arduboy/pocket-adventure-fx
   ```
4. **Build:**
   ```bash
   ./build.sh ../monkey
   ```
   The package ends up in `dist\PocketAdventureFX.arduboy` in the project folder.

| Problem | Fix |
|---|---|
| `'.' is not recognized` | You ran it in `cmd.exe`. Run it in the Ubuntu (WSL) window. |
| `\r: command not found` or other odd script errors | The files got Windows line endings: `sudo apt install dos2unix`, then `find . -name '*.sh' -o -name '*.py' -o -name Makefile \| xargs dos2unix` |
| The build can’t find the game files | The folder must directly contain `000.LFL` and `DISK0x.LEC` (see above). |

## Installing on the Arduboy

Flashing can replace the other games on your flashcart, so **back it up first**: in the
[Arduboy Toolset](https://github.com/randomouscrap98/arduboy_toolset/releases), read the
flashcart from the Arduboy and save it to a file.

1. Connect the Arduboy directly to the computer (not through a USB hub) and turn it on.
2. In the Arduboy Toolset: *File → Open cart editor*, drag
   `PocketAdventureFX.arduboy` into the window, then *File → Flash to Arduboy*.

Use the cart editor: on Windows, “Upload Sketch” stopped with a charmap decode error
for this package.

## Development

Put your copies into `config.mk` (not part of the repository):

```make
ORIGINALS := /path/to/MONKEY /path/to/MONKEY-DE
```

```bash
make            # FX data + sketch
make package    # dist/PocketAdventureFX.arduboy
make test       # unit tests and scene runs on the host test bench
make upload     # sketch and FX development data to the Arduboy FX
```

Converted images for checking end up in `build/preview/`.

**Upload tip:** connect the Arduboy directly to the computer. Behind some hubs it
does not re-enumerate after the reset into the bootloader and the upload waits forever.

## Tests

- `tests/test_advc.py`, `tests/test_scumm_text.py`: compiler, image encoding, path
  finding, text decoder. Tests that need the original data run only with `config.mk`.
- `tests/fxhost/`: the engine sources compiled for the host against a small
  replacement of Arduboy2/ArduboyFX (with frame buffer and font). It plays by pressing
  buttons and records rooms, texts, options, object states and characters.
  `tests/test_fxhost.py` plays every scene in every language of the build and compares
  each text with your copy – the repository only contains references.
- `tests/ardens_run.py`: the same scenes in the Ardens web player with the real
  `.arduboy` package (headless Chromium via Playwright). It reads the game state from
  the emulated RAM using the addresses in the ELF file and only releases a button once
  the game has seen it.

```bash
MI_ARDENS=<Ardens web player dir> MI_ARDENS_PYTHON=<python with playwright> \
    .venv/bin/python -m unittest tests.test_fxhost
```

## Project layout

```
build.sh                    one command: copies → dist/PocketAdventureFX.arduboy
game/game.adv               scene description (verbs, actors, objects, rooms, scripts, music)
game/art/ui/cursor.png      the only own graphic
tools/advc.py               compiler: game.adv + copies → game.bin + gamedata.h
tools/scumm_v4.py           reads rooms, walk boxes, objects, costumes and AdLib music
tools/scumm_text.py         reads all texts from the scripts (bytecode decoder)
tools/textsource.py         texts of a copy: language, character set, wrapping, bubbles
tools/original.py           1-bit conversion (backgrounds: Atkinson, characters: silhouettes)
tools/package.py            builds the .arduboy package
tools/vendor/               fxdata-build.py, fxdata-upload.py (MrBlinky, CC0)
PocketAdventureFX/          the sketch
  World.*                   languages, rooms, actors, walking, objects, flags, inventory
  Script.*                  bytecode interpreter (foreground script, two background routines)
  Ui.*                      cursor, sentence line, menu, speech, dialog options
  Sound.*                   music player (timer interrupt + tone timer)
tests/                      see above
```

## Architecture

**Everything that is content lives in the FX flash** and is streamed when needed:
backgrounds, sprites, texts, scripts, music, walk boxes. RAM holds only the changing
game state (actor positions, one byte per object, flags, small number variables,
inventory). The sketch needs about 21 KB of 29 KB program flash and 2 KB of 2.5 KB RAM
(1 KB of it is the screen buffer).

**Languages.** `game.bin` starts with a language directory. Everything that contains
text exists once per language (verbs, object names, scripts, rooms); images, walk boxes
and music are shared.

**One source for the data format.** `advc.py` defines every record once, packs the
binary data and generates the matching C++ structs with `static_assert`s. A build ID
detects outdated FX data.

**Scripts.** A small bytecode. One foreground script at a time (it blocks input, so
every action is a little cutscene) and up to two background routines (the cook, the
rat), which pause while the foreground runs. Subroutines, random branches, number and
string variables, and dialogs compiled to code. The compiler computes the call depth
and the maximum number of visible dialog options, so the engine reserves exactly the
RAM the game needs.

**Walking.** As in SCUMM, walkable areas are convex quadrilaterals. The compiler
computes which boxes touch and a next-box matrix; the engine walks from box to box.

**Original data.** `tools/scumm_v4.py` reads the SCUMM v4 containers of the VGA floppy
version (formats from the ScummVM source code). Rooms are scaled to 64 pixels height;
each room has its own 1-bit conversion settings (tone range, local contrast, colour
channel). Characters become light silhouettes slightly larger than the backgrounds.
Music: the melody voice of the AdLib tracks, played on the piezo by a 1 kHz timer
interrupt.

## Scene description

Line based, `#` starts a comment. Coordinates are room pixels after scaling.

```
music bar_music original 101
verb walk s22#5                                # the first verb must be walk
actor guybrush costume 1 scale 0.55            # the first actor is the player
object old_man o498.name
  on look
    say guybrush o498#1
  end
  on talk
    choose
      option r38.s202#14 if not told_name and not squinky
        say guybrush r38.s202#14
        set told_name
      option r38.s202#21
        done
    end
  end
end
room lookout original 38 tone 35 105 contrast 0.8
  walkboxes original
  place old_man original 498 face left
  entry
    put lookout 44 46
  end
end
```

Commands: `say`, `walk`, `put`, `face`, `remove`, `halt`, `costume`, `set`/`clear`,
`let`/`add` (`let x random a b`), `if … else … end` with `and`, `random`/`case [weight]`,
`repeat`, `choose`/`option`/`done`, `call` (subroutines `sub … end`), `start`/`stop`
(background `routine … end`), `play` (a `cutscene` from a routine), `room`, `card`,
`music`, `wait [random]`, `state`, `pickup`, `lose`, `hide`, `show`, `pan`, `flash`,
`setstring`/`setchar`. Conditions: flags, number comparisons, `has <object>`,
`<object> open`, `hover <object>`, `<actor> x|y <|> n`. Object handlers `on <verb>`,
`on <verb> <object>` and `on other`.

Texts are references to the original: `s22#5` (global script 22), `r38.s203#1`
(local script 203 of room 38), `r28.en#1` (entry script), `o498#1` (object script),
`o498.name` (object name), `…:2` (only the second speech bubble). List all texts of a
copy with `tools/scumm_text.py <copy>`. Own texts of the engine use `ui.<key>`.

## Thanks

- **spinal** ([community.arduboy.com](https://community.arduboy.com/t/the-first-scenes-of-the-secret-of-monkey-island-on-the-arduboy-fx/13756)):
  the Windows build guide above, the tip for extracting the original floppies, and the
  suggestion to use the Arduboy’s greyscale mode (work in progress).

## Legal

*The Secret of Monkey Island* is © Lucasfilm Games / Disney. This is an unofficial
fan project. The repository contains no original data. Built packages contain material
from your own copy and must not be distributed.

The engine, the compiler and the tools are free software under the
**GNU General Public License v3.0 or later** (see `LICENSE`).

- SCUMM formats after the source code of [ScummVM](https://github.com/scummvm/scummvm)
  and `descumm` from the ScummVM tools.
- `tools/vendor/fxdata-build.py`, `fxdata-upload.py`:
  [MrBlinky/Arduboy-Python-Utilities](https://github.com/MrBlinky/Arduboy-Python-Utilities), CC0.
- Arduboy2 and ArduboyFX (downloaded by `build.sh`): Arduboy-homemade-package by MrBlinky.
