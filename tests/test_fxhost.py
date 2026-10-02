"""Szenendurchläufe auf dem Host-Prüfstand (tests/fxhost).

Der Prüfstand führt die Engine mit dem gebauten game.bin aus und spielt per
Tastendruck: Verben wählen, Cursor auf Objekte, klicken. Erwartet wird eine
Folge von Ereignissen (Raum, Text, Dialogoption, Karte, Türzustand); Texte
stehen hier nur als Verweise (r28.s215#1) und werden zur Laufzeit aus der
eigenen Originalkopie aufgelöst – in jeder Sprache, die im Build steckt.

Aufruf: make test (baut Daten und Prüfstand vorher)

Dieselben Szenen laufen auch im Emulator Ardens, mit dem gebauten Paket
(tests/ardens_run.py, in Echtzeit, entsprechend langsam):
    MI_ARDENS=<Ardens-Web-Player> MI_ARDENS_PYTHON=<Python mit Playwright> \
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
    """Namen → Nummern aus gamedata.h (VERB_, OBJ_, ACTOR_, ROOM_)."""
    out = {}
    for kind, name, value in re.findall(r"constexpr uint8_t (VERB|OBJ|ACTOR|ROOM)_(\w+) = (\d+);",
                                        HEADER.read_text()):
        out[(kind, name.lower())] = int(value)
    return out


def languages():
    """Originalkopien in der Reihenfolge des Builds (= Sprachauswahl)."""
    if not STAMP.exists():
        return []
    return [Path(p) for p in STAMP.read_text().split()]


HAVE_BUILD = HOST.exists() and GAME.exists() and HEADER.exists() and languages()


# --- Ereignisse ---------------------------------------------------------------
#
# Erwartungen sind Tupel; say/option nehmen einen Textverweis, der je Sprache
# in seine Sprechblasen aufgelöst wird.

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


# --- Szenen -------------------------------------------------------------------
#
# Befehle des Prüfstands (siehe harness.cpp); Namen in {…} werden über
# gamedata.h zu Nummern: {o:bar_door}, {v:open}, {a:cook}.

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

# Vor der Küchentür, Verb gewählt, Cursor auf der Tür: Dann lässt sich auf
# den Koch warten und im richtigen Moment klicken.
AT_KITCHEN = INTO_BAR + """
walk 230 58
verb {v:open}
aim {o:kitchen_door}
"""

# Nach dem Vorspann wieder hinauf zum Ausguck
AT_LOOKOUT = START + """
do {v:walk} {o:cliffside}
idle
"""

LOOKOUT_TALK = [say("guybrush", f"r38.s203#{i}") if i in (1, 3, 5, 8, 10, 12) else say("lookout", f"r38.s203#{i}")
                for i in range(1, 13)]

SCENES = {
    # Vorspann bis zum Dock: Gespräch mit dem Ausguck, Kapitelkarte mit Musik
    "intro": (START, [room("lookout"), *LOOKOUT_TALK, ("music", "part1_music"), CARD, room("dock")]),

    # Späteres Gespräch mit dem Ausguck (r38.s202): Smalltalk, Schreck, Name
    # (der falsche Name #17), wer er ist, dann Gouverneur und Blindheit – danach ist er
    # beschäftigt.
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

    # Wiedersehen (#11) mit verballhorntem Namen (s141).
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

    # Stadtstraße (Raum 35): Uhr, Schild, gesperrter Torbogen zur Hauptstraße,
    # Rückweg zum Dock.
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

    # Voodoo-Lady (r29.s210): Name, Huhn, Zukunft; danach ist sie fort.
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

    # Die Piraten (r35.s216): Kartenangebot, Protokoll gegen zwei Achterstücke.
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

    # Die Ratte (s214/s215): Zeiger auf die Ratte, nah dran – fünfmal, dann
    # läuft sie davon.
    "rat": (IN_STREET + """
walk 40 60
""" + "frames 30\naim {o:rat_obj}\nidle\naim {o:men_obj}\n" * 5 + """
idle
""", [say("men", "r35.s215#1"), say("men", "r35.s215#2"), say("men", "r35.s215#3"), say("men", "r35.s215#4"),
      say("men", "r35.s215#6"), gone("rat")]),

    # Der Kartenverkäufer (r35.s218): Cousin Sven, dann das Angebot.
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

    # Karte von Mêlée (Raum 85): über den Pfad am Ausguck; nur Dorf und
    # Ausguck lassen sich betreten.
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

    # Die Bartür muss man erst öffnen (o437); geschlossen passiert nichts.
    "bar_door": (START + """
do {v:walk} {o:bar_entrance}
idle
do {v:open} {o:bar_entrance}
idle
do {v:walk} {o:bar_entrance}
idle
""", [state("bar_entrance", 1), state("bar_door", 1), room("scummbar")]),

    # Piratendialog (r28.s220): erst die Frage #12, nach Antwort #14 die
    # drei Prüfungen und gleich die Fragen; gefragte Themen heißen danach
    # #48 statt #47 usw., #56 und #57 verschwinden.
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

    # Beim ersten Verlassen der Bar (Bit 446): s117, das Geisterschiff. Beim
    # zweiten Mal geht es direkt aufs Dock.
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

    # Koch in der Küche (s211): Tür geht auf, Ruf von drinnen, Tür zu (s214);
    # in den zehn Sekunden danach (s212) lässt sie sich öffnen.
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

    # Koch draußen und weit weg (s203): Er bleibt stehen, die Tür geht auf.
    "kitchen_sneak": (AT_KITCHEN + """
until {a:cook} < 120
click
idle
do {v:walk} {o:kitchen_door}
idle
""", [state("kitchen_door", 1), room("kitchen")]),

    # Koch draußen und nah (s215): Warnung, dann zurück.
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
        # String-Variablen (z. B. der Name beim Ausguck) wie beim Bauen: aus
        # game.adv, damit Texte mit Platzhaltern genauso umbrechen.
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
                        # Variable (der verballhornte Name, das Geld): ein Wort beliebigen Inhalts
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
    """Graustufen: Das angezeigte Bild hat Grautöne (Pixel, die nur in einer
    oder zwei der drei Ebenen hell sind); im Menü auf Schwarz-Weiß
    umgeschaltet, ist jedes Pixel in allen Ebenen gleich."""

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
        # Text und Rechtecke zeichnet die Engine seitenweise (Common.h:
        # Arduboy::write, fillRect); der Prüfstand vergleicht mit der
        # Pixel-für-Pixel-Vorlage.
        r = subprocess.run([str(HOST), str(GAME)], input=b"drawcheck\n", capture_output=True, timeout=120)
        out = r.stdout.decode("cp437")
        self.assertEqual(r.returncode, 0, out[-2000:])
        self.assertIn(f"textcheck {4 * 73 * 3 * 256 + 4 * 5 * 7 * 5}", out)
        self.assertIn(f"rectcheck {2 * 75 * 9 * 4 * 5}", out)
        self.assertRegex(out, r"screencheck [1-9]\d+")

    def test_title_and_rooms_have_grey(self):
        # Erst das Titelbild (vor jedem Tastendruck; im ersten Frame ist eine
        # Ebene noch ungezeichnet), dann das Dock nach dem Vorspann
        tones, _ = self.tones("frames 2\ntones\n" + START.replace("{lang}", "0") + "tones\n")
        for name, (black, dark, light, white) in zip(("Titel", "Dock"), tones):
            with self.subTest(name):
                self.assertGreater(dark + light, 500, f"{name}: kaum Grau ({black}, {dark}, {light}, {white})")
                self.assertGreater(white, 100, f"{name}: kaum Weiß ({black}, {dark}, {light}, {white})")

    def test_speech_is_white_on_black(self):
        # Text ohne eigene Farbe (Sprechblasen) muss in allen drei Ebenen
        # stehen: ArduboyG-Weiß (3), nicht Arduboy2-Weiß (1) = Dunkelgrau.
        # Der Vorspann zeigt nach 200 Frames eine Sprechblase oben.
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
