"""Scene runs on the host test bench (tests/fxhost).

The test bench runs the engine with the built game.bin and plays via
key presses: pick verbs, cursor onto objects, click. Expected is a
sequence of events (room, text, dialogue option, card, door state); texts
appear here only as references (r28.s215#1) and are resolved at run time
from your own copy of the game – in every language contained in the build.

Run: make test (builds data and test bench first)

The same scenes also run in the Ardens emulator, with the built package
(tests/ardens_run.py, in real time, correspondingly slow):
    MI_ARDENS=<Ardens-Web-Player> MI_ARDENS_PYTHON=<Python with Playwright> \
        .venv/bin/python -m unittest tests.test_fxhost
"""
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import importlib.util  # noqa: E402

import scumm_text  # noqa: E402
import textsource  # noqa: E402

_spec = importlib.util.spec_from_file_location("advc", ROOT / "tools" / "advc.py")
advc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(advc)

HOST = ROOT / "build" / "fxhost"
GAME = ROOT / "PocketAdventureFX" / "fxdata" / "game.bin"
HEADER = ROOT / "PocketAdventureFX" / "gamedata.h"
STAMP = ROOT / "build" / "originals.txt"


def constants():
    """Names → numbers from gamedata.h (VERB_, OBJ_, ACTOR_, ROOM_)."""
    out = {}
    for kind, name, value in re.findall(r"constexpr uint8_t (VERB|OBJ|ACTOR|ROOM)_(\w+) = (\d+);",
                                        HEADER.read_text()):
        out[(kind, name.lower())] = int(value)
    return out


def languages():
    """Copies of the game in build order (= language selection)."""
    if not STAMP.exists():
        return []
    return [Path(p) for p in STAMP.read_text().split()]


HAVE_BUILD = HOST.exists() and GAME.exists() and HEADER.exists() and languages()


# --- Events -------------------------------------------------------------------
#
# Expectations are tuples; say/option take a text reference that is resolved
# into its speech bubbles per language.

def say(actor, ref):
    return ("say", actor, ref)


def narrator(ref):
    return ("say", None, ref)


def option(ref):
    return ("option", ref)


def room(name):
    return ("room", name)


def state(obj, n):
    return ("state", obj, n)


def gone(actor):
    return ("gone", actor)


CARD = ("card",)


# --- Scenes -------------------------------------------------------------------
#
# Test bench commands (see harness.cpp); names in {…} become numbers via
# gamedata.h: {o:bar_door}, {v:open}, {a:cook}.

START = """
lang {lang}
start
idle
"""

INTO_BAR = START + """
do {v:open} {o:bar_entrance}
idle
do {v:walk} {o:bar_entrance}
idle
"""

IN_STREET = START + """
do {v:walk} {o:archway}
idle
"""

TO_SHOP = IN_STREET + """
do {v:open} {o:shop_door}
idle
do {v:walk} {o:shop_door}
idle
"""

# In front of the kitchen door, verb picked, cursor on the door: then we can
# wait for the cook and click at the right moment.
AT_KITCHEN = INTO_BAR + """
walk 230 58
verb {v:open}
aim {o:kitchen_door}
"""

# After the intro back up to the lookout
AT_LOOKOUT = START + """
do {v:walk} {o:cliffside}
idle
"""

LOOKOUT_TALK = [say("guybrush", f"r38.s203#{i}") if i in (1, 3, 5, 8, 10, 12) else say("lookout", f"r38.s203#{i}")
                for i in range(1, 13)]

SCENES = {
    # Intro up to the dock: talk with the lookout, chapter card with music
    "intro": (START, [room("lookout"), *LOOKOUT_TALK, ("music", "part1_music"), CARD, room("dock")]),

    # Later talk with the lookout (r38.s202): small talk, fright, name
    # (the wrong name #17), who he is, then governor and blindness – after that he is
    # busy.
    "lookout_talk": (AT_LOOKOUT + """
do {v:talk} {o:old_man}
choose 3
choose 1
choose 2
choose 0
choose 0
choose 1
idle
do {v:talk} {o:old_man}
idle
""", [option("r38.s202#5"), option("r38.s202#8"), say("guybrush", "r38.s202#8"),
      say("lookout", "r38.s202#10"), say("lookout", "r38.s202#12"), say("lookout", "r38.s202#13"),
      option("r38.s202#14"), option("r38.s202#17"), option("r38.s202#18"), option("r38.s202#19"),
      option("r38.s202#21"),
      say("lookout", "r38.s202#26"), option("r38.s202#15"),
      say("lookout", "r38.s202#31"), say("lookout", "r38.s202#33"),
      option("r38.s202#37"), option("r38.s202#38"), option("r38.s202#39"), option("r38.s202#40"),
      say("lookout", "r38.s202#41"), say("lookout", "r38.s202#42"), say("guybrush", "r38.s202#40"),
      say("lookout", "r38.s202#49"), say("lookout", "r38.s202#4")]),

    # Reunion (#11) with mangled name (s141).
    "lookout_again": (AT_LOOKOUT + """
do {v:talk} {o:old_man}
choose 0
choose 4
idle
do {v:talk} {o:old_man}
choose 1
choose 4
idle
""", [say("lookout", "r38.s202#49"), say("guybrush", "r38.s202#6"), say("lookout", "r38.s202#10"),
      say("lookout", "r38.s202#11"), option("r38.s202#14"), say("lookout", "r38.s202#49")]),

    # Town street (room 35): clock, sign, blocked archway to the high street,
    # way back to the dock.
    "street": (START + """
do {v:walk} {o:archway}
idle
do {v:look} {o:clock}
idle
do {v:look} {o:sign}
idle
do {v:walk} {o:high_street}
idle
do {v:walk} {o:street_archway}
idle
do {v:walk} {o:archway}
idle
do {v:look} {o:clock}
idle
""", [room("street"), say("guybrush", "r35.s210#1"), say("guybrush", "o451#1"), narrator("ui.not_included"),
      room("dock"), room("street"), say("guybrush", "r35.s210#2")]),

    # Voodoo lady (r29.s210): name, chicken, future; then she is gone.
    "voodoo": (TO_SHOP + """
do {v:look} {o:chicken}
idle
do {v:pickup} {o:rubber_chicken}
idle
do {v:talk} {o:fortune_teller}
choose 0
choose 1
choose 0
choose 0
choose 0
idle
do {v:talk} {o:fortune_teller}
choose 0
idle
""", [room("voodoo"), say("guybrush", "o391#1"), say("guybrush", "o391#4"), say("voodoo", "r29.s210#1"),
      say("voodoo", "r29.s210#10"), say("guybrush", "r29.s210#14"), say("voodoo", "r29.s210#19"),
      say("voodoo", "r29.s210#20"), say("voodoo", "r29.s210#29"), say("voodoo", "r29.s210#45"),
      gone("voodoo"), say("guybrush", "r29.s210#46"), option("r29.s210#48"), say("guybrush", "r29.s210#51")]),

    # The pirates (r35.s216): map offer, minutes for two pieces of eight.
    "men": (IN_STREET + """
walk 50 60
do {v:talk} {o:men_obj}
choose 2
choose 2
choose 3
idle
inv {v:look} {o:money_obj}
idle
""", [say("guybrush", "r35.s216#8"), say("men", "r35.s216#18"), say("men", "r35.s216#22"),
      say("men", "r35.s216#28"), say("guybrush", "o497#2")]),

    # The rat (s214/s215): pointer on the rat, close by – five times, then
    # it runs away.
    "rat": (IN_STREET + """
walk 40 60
""" + "frames 30\naim {o:rat_obj}\nidle\naim {o:men_obj}\n" * 5 + """
idle
""", [say("men", "r35.s215#1"), say("men", "r35.s215#2"), say("men", "r35.s215#3"), say("men", "r35.s215#4"),
      say("men", "r35.s215#6"), gone("rat")]),

    # The map seller (r35.s218): cousin Sven, then the offer.
    "citizen": (IN_STREET + """
do {v:talk} {o:citizen_obj}
choose 3
choose 0
idle
do {v:talk} {o:citizen_obj}
choose 1
idle
""", [say("citizen", "r35.s218#13"), say("citizen", "r35.s218#20"), say("citizen", "r35.s218#30"),
      say("citizen", "r35.s218#35"), say("citizen", "r35.s218#1"), say("citizen", "r35.s218#3"),
      say("citizen", "r35.s218#38")]),

    # Map of Mêlée (room 85): via the path at the lookout; only village and
    # lookout can be entered.
    "map": (START + """
do {v:walk} {o:cliffside}
idle
do {v:walk} {o:path}
idle
do {v:walk} {o:map_house}
idle
do {v:walk} {o:map_clearing}
idle
do {v:walk} {o:map_lookout}
idle
do {v:walk} {o:path}
idle
do {v:walk} {o:map_village}
idle
""", [room("lookout"), room("map"), ("music", "map_music"), narrator("ui.not_included"), narrator("ui.not_included"),
      room("lookout"), room("map"), room("dock")]),

    # The bar door must be opened first (o437); closed, nothing happens.
    "bar_door": (START + """
do {v:walk} {o:bar_entrance}
idle
do {v:open} {o:bar_entrance}
idle
do {v:walk} {o:bar_entrance}
idle
""", [state("bar_entrance", 1), state("bar_door", 1), room("scummbar")]),

    # Pirate dialogue (r28.s220): first question #12, after answer #14 the
    # three trials and straight on the questions; asked topics are then
    # #48 instead of #47 etc., #56 and #57 disappear.
    "leaders": (INTO_BAR + """
do {v:talk} {o:leaders_obj}
choose 1
choose 0
choose 3
choose 4
idle
do {v:talk} {o:leaders_obj}
choose 4
idle
""", [say("leaders", "r28.s220#12"), option("r28.s220#13"), option("r28.s220#14"), option("r28.s220#15"),
      say("guybrush", "r28.s220#14"), say("guybrush", "r28.s220#23"), say("guybrush", "r28.s220#30"),
      say("leaders", "r28.s220#39"),
      option("r28.s220#47"), option("r28.s220#50"), option("r28.s220#53"), option("r28.s220#56"),
      option("r28.s220#57"), option("r28.s220#58"),
      say("leaders", "r28.s220#59"), say("leaders", "r28.s220#62"),
      option("r28.s220#48"),
      say("guybrush", "r28.s220#56"), say("leaders", "r28.s220#88"),
      option("r28.s220#48"), option("r28.s220#50"), option("r28.s220#53"), option("r28.s220#57"),
      option("r28.s220#58"),
      say("guybrush", "r28.s220#58"), say("leaders", "r28.s220#107"),
      say("leaders", "r28.s220#3"), option("r28.s220#48"), say("leaders", "r28.s220#106")]),

    # On first leaving the bar (bit 446): s117, the ghost ship. The
    # second time it goes straight to the dock.
    "ghost_ship": (INTO_BAR + """
do {v:open} {o:bar_door}
idle
do {v:walk} {o:bar_door}
idle
do {v:walk} {o:bar_entrance}
idle
do {v:walk} {o:bar_door}
idle
""", [room("meanwhile"), narrator("s117#1"), room("ghostship"), ("music", "ghost_music"), narrator("s117#3"),
      room("ghostcabin"), say("ghost", "s117#5"), say("lechuck", "s117#6"), say("lechuck", "s117#14"),
      say("ghost", "s117#16"), say("lechuck", "s117#18"), say("ghost", "s117#19"), room("dock"),
      room("scummbar"), room("dock")]),

    # Cook in the kitchen (s211): door opens, call from inside, door shut (s214);
    # in the ten seconds after that (s212) it can be opened.
    "kitchen_called": (INTO_BAR + """
do {v:open} {o:kitchen_door}
idle
frames 60
do {v:open} {o:kitchen_door}
idle
do {v:walk} {o:kitchen_door}
idle
""", [state("kitchen_door", 1), narrator("r28.s214#1"), state("kitchen_door", 0),
      state("kitchen_door", 1), room("kitchen")]),

    # Cook outside and far away (s203): he stops, the door opens.
    "kitchen_sneak": (AT_KITCHEN + """
until {a:cook} < 120
click
idle
do {v:walk} {o:kitchen_door}
idle
""", [state("kitchen_door", 1), room("kitchen")]),

    # Cook outside and close (s215): warning, then back.
    "kitchen_caught": (AT_KITCHEN + """
until {a:cook} < 200
until {a:cook} > 200
click
idle
""", [say("cook", "r28.s215#1"), state("kitchen_door", 0), gone("cook")]),
}


@unittest.skipUnless(HAVE_BUILD, "kein Build mit Originaldaten (make test baut ihn)")
class Scenes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = constants()
        cls.sources = [textsource.TextSource(d) for d in languages()]
        # String variables (e.g. the name at the lookout) as in the build: from
        # game.adv, so texts with placeholders wrap the same way.
        game = advc.Game(ROOT / "game")
        game.original_dir = languages()[0]
        game.languages = cls.sources
        advc.Parser(game, advc.tokenize((ROOT / "game" / "game.adv").read_text(encoding="utf-8"))).parse()
        compiler = advc.Compiler(game)
        for name in game.numbers:
            compiler.var(None, name)
        for src in cls.sources:
            src.strings = compiler.string_widths(src)
            src.numbers = {orig: game.vars[name] for name, orig in game.numbers.items()}

    def number(self, kind, name):
        key = ({"v": "VERB", "o": "OBJ", "a": "ACTOR", "r": "ROOM"}[kind], name)
        if key not in self.names:
            self.fail(f"{kind}:{name} gibt es in gamedata.h nicht")
        return self.names[key]

    def commands(self, text, lang):
        text = text.replace("{lang}", str(lang))
        return re.sub(r"\{(\w):(\w+)\}", lambda m: str(self.number(m.group(1), m.group(2))), text)

    def run_host(self, commands):
        if os.environ.get("MI_ARDENS"):
            cmd = [os.environ.get("MI_ARDENS_PYTHON", sys.executable), str(ROOT / "tests" / "ardens_run.py"),
                   os.environ["MI_ARDENS"]]
            timeout = 1800
        else:
            cmd, timeout = [str(HOST), str(GAME)], 120
        r = subprocess.run(cmd, input=commands.encode(), capture_output=True, timeout=timeout)
        out = r.stdout.decode("cp437")
        if r.returncode or "FEHLER" in out:
            self.fail(f"Prüfstand: Exit {r.returncode}\n{out[-2000:]}")
        return [line for line in out.splitlines() if not line.startswith(">")]

    def expected_lines(self, events, src):
        music = {m: i for i, m in enumerate(self.music_names())}
        out = []
        for e in events:
            kind = e[0]
            if kind == "say":
                who = "-" if e[1] is None else str(self.number("a", e[1]))
                if e[2].startswith("ui."):
                    text = advc.UI_TEXT.get(src.code, advc.UI_TEXT["en"])[e[2][3:]]
                    out.append(f"say {who} {' '.join(textsource.wrap(text, 21))}")
                    continue
                for b in src.bubbles(e[2], 21, 4):
                    line = f"say {who} {b.replace(chr(10), ' ')}"
                    marker = f"[{re.escape(scumm_text.STRING_VAR + scumm_text.INT_VAR)}]."
                    if re.search(marker, line):
                        # variable (the mangled name, the money): one word of any content
                        parts = re.split(marker, line)
                        out.append(("re", re.compile(r"\S+".join(map(re.escape, parts)) + "$")))
                    else:
                        out.append(line)
            elif kind == "option":
                out.append(("option", src.line(e[1])))
            elif kind == "room":
                out.append(f"room {self.number('r', e[1])}")
            elif kind == "state":
                out.append(f"state {self.number('o', e[1])} {e[2]}")
            elif kind == "gone":
                out.append(f"gone {self.number('a', e[1])}")
            elif kind == "music":
                out.append(f"music {music[e[1]]}")
            elif kind == "card":
                out.append("card")
        return out

    def music_names(self):
        found = re.findall(r"constexpr uint8_t MUSIC_(\w+) = (\d+);", HEADER.read_text())
        names = [""] * (max(int(v) for _, v in found) + 1)
        for n, v in found:
            names[int(v)] = n.lower()
        return names

    def assertSubsequence(self, expected, trace, scene, lang):
        i = 0
        for line in trace:
            if i == len(expected):
                break
            want = expected[i]
            if isinstance(want, tuple) and want[0] == "re":
                hit = bool(want[1].match(line))
            elif isinstance(want, tuple):
                hit = line.startswith("option ") and line.split(" ", 2)[2] == want[1]
            else:
                hit = line == want
            if hit:
                i += 1
        if i < len(expected):
            self.fail(f"{scene} [{lang}]: erwartet {expected[i]!r}, nach {expected[:i][-3:]!r}\n"
                      + "\n".join(trace[-40:]))

    def test_scenes(self):
        only = [x for x in os.environ.get("MI_SCENES", "").split(",") if x]
        for lang, src in enumerate(self.sources):
            for scene, (commands, events) in SCENES.items():
                if only and scene not in only:
                    continue
                with self.subTest(scene=scene, lang=src.name):
                    trace = self.run_host(self.commands(commands, lang))
                    self.assertSubsequence(self.expected_lines(events, src), trace, scene, src.name)


@unittest.skipUnless(HAVE_BUILD, "kein Build mit Originaldaten (make test baut ihn)")
@unittest.skipIf(os.environ.get("MI_ARDENS"), "nur auf dem Host-Prüfstand (Bildzählung)")
class Greyscale(unittest.TestCase):
    """Greyscale: the displayed image has grey tones (pixels lit in only one
    or two of the three planes); switched to black and white in the
    menu, every pixel is the same in all planes."""

    def tones(self, commands):
        r = subprocess.run([str(HOST), str(GAME)], input=commands.encode(), capture_output=True, timeout=120)
        out = r.stdout.decode("cp437")
        if r.returncode or "FEHLER" in out:
            self.fail(f"Prüfstand: Exit {r.returncode}\n{out[-2000:]}")
        lines = [line for line in out.splitlines() if not line.startswith(">")]
        tones = [tuple(map(int, line.split()[1:])) for line in lines if line.startswith("tones ")]
        switched = [line for line in lines if line.startswith("greyscale ")]
        return tones, switched

    def test_fast_drawing_matches_pixel_by_pixel(self):
        # The engine draws text and rectangles page-wise (Common.h:
        # Arduboy::write, fillRect); the test bench compares with the
        # pixel-by-pixel reference.
        r = subprocess.run([str(HOST), str(GAME)], input=b"drawcheck\n", capture_output=True, timeout=120)
        out = r.stdout.decode("cp437")
        self.assertEqual(r.returncode, 0, out[-2000:])
        self.assertIn(f"textcheck {4 * 73 * 3 * 256 + 4 * 5 * 7 * 5}", out)
        self.assertIn(f"rectcheck {2 * 75 * 9 * 4 * 5}", out)
        self.assertRegex(out, r"screencheck [1-9]\d+")

    def test_title_and_rooms_have_grey(self):
        # First the title screen (before any key press; in the first frame one
        # plane is still undrawn), then the dock after the intro
        tones, _ = self.tones("frames 2\ntones\n" + START.replace("{lang}", "0") + "tones\n")
        for name, (black, dark, light, white) in zip(("Titel", "Dock"), tones):
            with self.subTest(name):
                self.assertGreater(dark + light, 500, f"{name}: kaum Grau ({black}, {dark}, {light}, {white})")
                self.assertGreater(white, 100, f"{name}: kaum Weiß ({black}, {dark}, {light}, {white})")

    def test_speech_is_white_on_black(self):
        # Text without its own colour (speech bubbles) must be in all three
        # planes: ArduboyG white (3), not Arduboy2 white (1) = dark grey.
        # The intro shows a speech bubble at the top after 200 frames.
        tones, _ = self.tones("lang 0\nframes 200\ntones 12 0 116 24\n")
        black, dark, light, white = tones[0]
        self.assertEqual(dark + light, 0, f"Grau in der Sprechblase: {tones[0]}")
        self.assertGreater(white, 300)

    def test_menu_switches_to_black_and_white_and_back(self):
        tones, switched = self.tones(START.replace("{lang}", "0") + "grey\nframes 1\ntones\ngrey\nframes 1\ntones\n")
        self.assertEqual(switched, ["greyscale 0", "greyscale 1"])
        mono, grey = tones
        self.assertEqual(mono[1] + mono[2], 0, f"Schwarz-Weiß mit Grautönen: {mono}")
        self.assertGreater(grey[1] + grey[2], 500, f"wieder Graustufen, aber kaum Grau: {grey}")


if __name__ == "__main__":
    unittest.main()
