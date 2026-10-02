"""Tests für den Adventure-Compiler (tools/advc.py).

Aufruf: make test
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("advc", ROOT / "tools" / "advc.py")
advc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(advc)

import numpy as np  # noqa: E402

import original as orig  # noqa: E402
import scumm_text  # noqa: E402
import textsource  # noqa: E402

MINIMAL = """
verb walk s22#5
verb use s22#9 prep s22#26
actor hero sprite "hero.png"
object stone o1.name
  on use
    say hero r1.s200#1
  end
end
room start bg "bg.png"
  walkbox 0 40 127 63
  place stone at 10 50 8 8 walkto 20 55 face left
  entry
    put hero 60 55
  end
end
start start
"""

# Texte der Test-Sprachfassungen; '|' trennt Sprechblasen wie die
# Warte-Codes des Originals.
TEXTS = {
    "en": {"s22#5": "Walk to", "s22#9": "Use", "s22#26": "with", "o1.name": "stone",
           "r1.s200#1": "Hello", "r1.s200#2": "Here I am", "r1.s200#3": "Yes|No",
           "r1.s200#4": "Why does the lighthouse keeper whistle every midnight?",
           "r1.s200#5": "Bye",
           "r1.s200#6": "Why does the old lighthouse keeper whistle so loudly at midnight, even in the rain?",
           "r1.s200#7": "Why does the old lighthouse keeper whistle so loudly at midnight, even in the rain, "
                        "and why does nobody in this little harbour ever tell me anything about it?"},
    "de": {"s22#5": "Gehe zu", "s22#9": "Benutze", "s22#26": "mit", "o1.name": "Stein",
           "r1.s200#1": "Schöne Grüße", "r1.s200#2": "Da bin ich", "r1.s200#3": "Ja|Nein",
           "r1.s200#4": "Warum pfeift der Leuchtturmwärter jede Nacht um zwölf?",
           "r1.s200#5": "Tschüss",
           "r1.s200#6": "Warum pfeift der alte Leuchtturmwärter jede Nacht um zwölf so laut, auch im Regen?",
           "r1.s200#7": "Warum pfeift der alte Leuchtturmwärter jede Nacht um zwölf so laut, auch im Regen, "
                        "und warum erzählt mir in diesem kleinen Hafen eigentlich niemand etwas davon?"},
}
NAMES = {"en": "English", "de": "Deutsch"}


class FakeSource(textsource.TextSource):
    """TextSource ohne Originalkopie: Texte aus TEXTS."""

    def __init__(self, code, texts=None):
        self.code, self.name = code, NAMES[code]
        self.texts = {}
        self.strings = {}
        self.numbers = {}
        for ident, text in (texts or TEXTS[code]).items():
            parts = []
            for i, page in enumerate(text.split("|")):
                if i:
                    parts.append((scumm_text.WAIT, None))
                parts.append(page)
            self.texts[ident] = scumm_text.Text(ident, "print", parts, 0)


def compile_source(source, extra_files=None, langs=("en",)):
    """Kompiliert eine .adv-Quelle in einem Temp-Verzeichnis mit Standardbildern."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        Image.new("RGBA", (128, 64), (0, 0, 0, 255)).save(base / "bg.png")
        Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(base / "hero.png")
        for name, writer in (extra_files or {}).items():
            writer(base / name)
        game = advc.Game(base)
        game.languages = [FakeSource(code) for code in langs]
        advc.Parser(game, advc.tokenize(textwrap.dedent(source))).parse()
        comp = advc.Compiler(game)
        return comp, comp.compile()


def read_record(data, name, offset):
    fields = {}
    for field, typ in advc.RECORDS[name]:
        n = advc.SIZES[typ]
        fields[field] = int.from_bytes(data[offset:offset + n], "little")
        offset += n
    return fields


def header(data, lang=0):
    """GameHeader der Sprache Nr. lang über das Sprachverzeichnis."""
    entry = read_record(data, "LangEntry", advc.record_size("LangDir") + lang * advc.record_size("LangEntry"))
    return read_record(data, "GameHeader", entry["header"])


def bitmap(data, address, frame=0, masked=False):
    """FX-Bild aus game.bin → Liste von Zeilen (Pixel 0/1) eines Frames."""
    w = int.from_bytes(data[address:address + 2], "big")
    h = int.from_bytes(data[address + 2:address + 4], "big")
    pages = (h + 7) // 8
    step = 2 if masked else 1
    base = address + 4 + frame * pages * w * step
    return [[(data[base + ((y // 8) * w + x) * step] >> (y & 7)) & 1 for x in range(w)] for y in range(h)]


def cstring(data, at):
    return data[at:data.index(0, at)]


def says(data, script):
    """Texte der SAY-Befehle am Anfang eines Skripts."""
    out = []
    while data[script] == advc.OP["SAY"]:
        out.append(cstring(data, int.from_bytes(data[script + 2:script + 5], "little")).decode("cp437"))
        script += 5
    return out


class ImageEncodingTest(unittest.TestCase):
    def test_matches_fxdata_build(self):
        """Unsere Bildkodierung muss byte-identisch zu fxdata-build.py sein."""
        import random
        rnd = random.Random(7)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # 3 Frames à 7x11 (Höhe kein Vielfaches von 8), mit Transparenz
            img = Image.new("RGBA", (21, 11))
            img.putdata([rnd.choice([(255, 255, 255, 255), (0, 0, 0, 255), (0, 0, 0, 0)])
                         for _ in range(21 * 11)])
            name = "probe_7x11.png"
            img.save(base / name)
            (base / "fx.txt").write_text(f'image_t probe = "{name}"\n')
            subprocess.run([sys.executable, str(ROOT / "tools" / "vendor" / "fxdata-build.py"),
                            str(base / "fx.txt")], check=True, capture_output=True)
            reference = (base / "fx-data.bin").read_bytes()
            ours, w, h, frames = advc.encode_image(base / name)
            self.assertEqual((w, h, frames), (7, 11, 3))
            self.assertEqual(ours, reference)


class MusicTest(unittest.TestCase):
    def test_timer_values(self):
        # f = 1 MHz / (OCR + 1)
        self.assertEqual(advc.encode_track([(1000, 10)]), [(999, 10)])
        self.assertEqual(advc.encode_track([(0, 5)]), [(0, 5)])

    def test_long_notes_are_split(self):
        self.assertEqual(advc.encode_track([(440, 600)]), [(2272, 255), (2272, 255), (2272, 90)])


class TokenizerTest(unittest.TestCase):
    def test_hash_in_reference_is_no_comment(self):
        self.assertEqual(advc.tokenize("say hero r38.s203#1  # Kommentar")[0].tokens, ["say", "hero", "r38.s203#1"])

    def test_comment_and_quotes(self):
        self.assertEqual(advc.tokenize("# nur Kommentar\ncursor \"a # b.png\" # c")[0].tokens,
                         ["cursor", "a # b.png"])


class CompilerTest(unittest.TestCase):
    def test_minimal_game_layout(self):
        comp, data = compile_source(MINIMAL)
        lang = read_record(data, "LangDir", 0)
        self.assertEqual((lang["magic"], lang["buildId"], lang["count"]), (0x464D, comp.build_id, 1))
        hdr = header(data)
        self.assertEqual((hdr["verbCount"], hdr["objectCount"], hdr["actorCount"], hdr["roomCount"]), (2, 1, 1, 1))
        room = read_record(data, "RoomRec", hdr["rooms"])
        self.assertEqual(room["width"], 128)
        place = read_record(data, "PlaceRec", room["places"])
        self.assertEqual((place["x"], place["y"], place["w"], place["h"], place["face"]), (10, 50, 8, 8, 1))
        # Startskript (Kurzform): ROOM 0 ohne Ankunftsposition, END
        start = hdr["startScript"]
        self.assertEqual(data[start:start + 7], bytes([advc.OP["ROOM"], 0, 0xFF, 0xFF, 0, 0, advc.OP["END"]]))
        self.assertEqual(cstring(data, hdr["uiEmpty"]), b"(empty)")

    def test_start_block_places_player(self):
        src = MINIMAL.replace("start start", "start\n  room start at 60 50 face left\n  say hero r1.s200#2\nend")
        _, data = compile_source(src)
        start = header(data)["startScript"]
        self.assertEqual(data[start:start + 6], bytes([advc.OP["ROOM"], 0, 60, 0, 50, advc.DIRS["left"]]))
        self.assertEqual(says(data, start + 6), ["Here I am"])

    def test_entry_must_not_change_room(self):
        src = MINIMAL.replace("    put hero 60 55", "    room start")
        with self.assertRaisesRegex(advc.CompileError, "Entry-Skripte dürfen keinen Raum wechseln"):
            compile_source(src)

    def test_verb_handler_points_to_say(self):
        _, data = compile_source(MINIMAL)
        hdr = header(data)
        obj = read_record(data, "ObjectRec", hdr["objects"])
        self.assertEqual(cstring(data, obj["name"]), b"stone")
        entry = read_record(data, "VerbEntry", obj["verbs"])
        self.assertEqual((entry["verb"], entry["other"]), (1, advc.NONE8))
        self.assertEqual(says(data, entry["script"]), ["Hello"])

    def test_two_languages_share_images(self):
        comp, data = compile_source(MINIMAL, langs=("en", "de"))
        self.assertEqual(read_record(data, "LangDir", 0)["count"], 2)
        en, de = header(data, 0), header(data, 1)
        self.assertNotEqual(en["objects"], de["objects"])
        self.assertEqual((en["actors"], en["music"], en["title"]), (de["actors"], de["music"], de["title"]))
        script = read_record(data, "VerbEntry", read_record(data, "ObjectRec", de["objects"])["verbs"])["script"]
        self.assertEqual(says(data, script), ["Schöne Grüße"])          # CP437-Umlaute
        names = [cstring(data, read_record(data, "LangEntry", advc.record_size("LangDir") + i * 6)["name"])
                 for i in range(2)]
        self.assertEqual(names, [b"English", b"Deutsch"])
        self.assertEqual(len(comp.images), 2)                         # Hintergrund, Figur: einmal

    def test_quoted_text_is_rejected(self):
        bad = MINIMAL.replace("say hero r1.s200#1", 'say hero "Hallo"')
        with self.assertRaisesRegex(advc.CompileError, "Verweis auf die Originaldaten"):
            compile_source(bad)

    def test_unknown_reference_names_the_line(self):
        bad = MINIMAL.replace("r1.s200#1", "r1.s200#99")
        with self.assertRaisesRegex(advc.CompileError, r"Zeile \d+: r1.s200#99: gibt es in der Fassung English nicht"):
            compile_source(bad)

    def test_waits_and_long_texts_become_bubbles(self):
        src = MINIMAL.replace("say hero r1.s200#1", "say hero r1.s200#3")
        _, data = compile_source(src)
        script = read_record(data, "VerbEntry", read_record(data, "ObjectRec", header(data)["objects"])["verbs"])["script"]
        self.assertEqual(says(data, script), ["Yes", "No"])
        src = MINIMAL.replace("say hero r1.s200#1", "say hero r1.s200#3:2")
        _, data = compile_source(src)
        script = read_record(data, "VerbEntry", read_record(data, "ObjectRec", header(data)["objects"])["verbs"])["script"]
        self.assertEqual(says(data, script), ["No"])
        src = FakeSource("en", {"s9#1": "Wort " * 30})
        bubbles = src.bubbles("s9#1", advc.TEXT_COLS, advc.TEXT_ROWS)
        self.assertEqual(len(bubbles), 2)
        self.assertTrue(all(len(line) <= advc.TEXT_COLS for b in bubbles for line in b.split("\n")))

    def test_header_structs_match_records(self):
        comp, _ = compile_source(MINIMAL)
        hdr = comp.header()
        for name in advc.RECORDS:
            size = advc.record_size(name)
            self.assertIn(f"static_assert(sizeof({name}) == {size}", hdr)
        lengths = re.search(r"OP_LENGTH\[\] = \{([^}]*)\}", hdr).group(1)
        self.assertEqual(len(lengths.split(",")), len(advc.OPCODES))

    def test_errors_name_the_line(self):
        bad = MINIMAL.replace("say hero", "say nobody")
        with self.assertRaisesRegex(advc.CompileError, r"Zeile \d+: Actor 'nobody' unbekannt"):
            compile_source(bad)

    def test_walk_must_be_first_verb(self):
        bad = MINIMAL.replace("verb walk s22#5\nverb use s22#9 prep s22#26",
                              "verb use s22#9 prep s22#26\nverb walk s22#5")
        with self.assertRaisesRegex(advc.CompileError, "erste Verb muss 'walk'"):
            compile_source(bad)

    def test_choose_with_done(self):
        src = MINIMAL.replace("    say hero r1.s200#1\n", textwrap.dedent('''\
            choose
              option r1.s200#2 if not seen
                set seen
              option r1.s200#5
                done
            end
        '''))
        comp, data = compile_source(src)
        self.assertIn("seen", comp.g.flags)

    def test_options_must_fit_the_display(self):
        # Die Liste zeigt höchstens CHOICE_ROWS Optionen und scrollt; darüber
        # steht der volle Text der gewählten, die Liste schrumpft dafür bis auf
        # eine Zeile. Sieben Zeilen Text passen also, acht nicht.
        def choose(numbers):
            options = "".join(f"  option r1.s200#{n}\n    done\n" for n in numbers)
            return MINIMAL.replace("    say hero r1.s200#1\n", f"    choose\n{options}    end\n")
        compile_source(choose((1, 2, 4, 5, 6, 2)), langs=("en", "de"))
        with self.assertRaisesRegex(advc.CompileError, "passt das nicht auf das Display"):
            compile_source(choose((7, 1)), langs=("en", "de"))
        # Gleichzeitig sichtbar: ohne Bedingungen alle; die Engine hält bis zu
        # MAX_OPTIONS (und bekommt so viel Platz, wie das Spiel braucht).
        comp, _ = compile_source(choose((1,) * 5))
        self.assertIn("constexpr uint8_t MAX_OPTIONS = 5;", comp.header())
        with self.assertRaisesRegex(advc.CompileError, "bis zu 17 Optionen gleichzeitig"):
            compile_source(choose((1,) * 17))

    def test_visible_options_bound(self):
        # Sich ausschließende Bedingungen zählen nur einmal.
        opts = [{"cond": [("flag", "a", False)]}, {"cond": [("flag", "a", True)]},
                {"cond": [("flag", "a", False), ("flag", "b", False)]}, {"cond": None}]
        self.assertEqual(advc.Compiler.visible_bound(opts), 3)

    def test_and_conditions_and_random(self):
        # „and“ wird zu je einem Sprung pro Teilbedingung; random springt über
        # eine Tabelle mit einem Ziel je Fall.
        body = textwrap.dedent('''\
            if seen and not has stone
              set done
            end
            random
            case
              set a
            case
            case
              set b
            end
        ''')
        src = MINIMAL.replace("    say hero r1.s200#1\n", textwrap.indent(body, "    "))
        _, data = compile_source(src)
        script = read_record(data, "VerbEntry", read_record(data, "ObjectRec", header(data)["objects"])["verbs"])["script"]
        self.assertEqual(data[script], advc.OP["JUNLESS"])
        self.assertEqual(data[script + 1], advc.COND_FLAG)
        self.assertEqual(data[script + 6], advc.OP["JUNLESS"])
        self.assertEqual(data[script + 7], advc.COND_HAS | advc.COND_NOT)
        at = script + 12 + 2                      # nach den Sprüngen: SET done
        self.assertEqual(data[at], advc.OP["RANDOM"])
        self.assertEqual(data[at + 1], 3)

    def test_string_variables(self):
        # string … original <nr> + setstring/setchar; die Puffergröße folgt
        # aus dem längsten gesetzten Text.
        body = "    setstring name r1.s200#2\n    setchar name 0 104\n"
        src = "string name original 30\n" + MINIMAL.replace("    say hero r1.s200#1\n", body)
        comp, _ = compile_source(src)
        self.assertIn("constexpr uint8_t STRING_SIZE = 10;", comp.header())   # „Here I am“ + NUL
        with self.assertRaisesRegex(advc.CompileError, "außerhalb des Strings"):
            compile_source(src.replace("setchar name 0", "setchar name 9"))
        with self.assertRaisesRegex(advc.CompileError, "wird nie gesetzt"):
            compile_source("string other original 31\n" + src)

    def test_decor_has_no_object(self):
        src = MINIMAL.replace("  place stone at", '  place decor at 40 50 image "hero.png"\n  place stone at')
        _, data = compile_source(src)
        room = read_record(data, "RoomRec", header(data)["rooms"])
        self.assertEqual(read_record(data, "PlaceRec", room["places"])["object"], advc.NONE8)
        self.assertEqual(room["placeCount"], 2)


class GreyTest(unittest.TestCase):
    """Graustufen-Umrechnung (original.to_grey) an künstlichen Bildern."""

    @staticmethod
    def flat_image(size, color):
        return Image.new("RGB", size, color)

    def test_ramp_gives_all_four_levels_in_order(self):
        ramp = Image.linear_gradient("L").rotate(90, expand=True).convert("RGB").resize((256, 64))
        levels = orig.to_grey(ramp, (128, 64), [{"tone": (0, 255)}])
        row = levels[0]
        self.assertEqual(sorted(set(row.tolist())), [0, 1, 2, 3])
        self.assertTrue((row[1:] >= row[:-1]).all() or (row[1:] <= row[:-1]).all())
        self.assertTrue((levels == levels[0]).all())

    def test_max_caps_the_brightest_level(self):
        white = self.flat_image((256, 64), (255, 255, 255))
        self.assertEqual(orig.to_grey(white, (128, 64), [{"tone": (0, 255), "max": 2}]).max(), 2)

    def test_gamma_darkens_the_middle(self):
        grey = self.flat_image((256, 64), (128, 128, 128))
        plain = orig.to_grey(grey, (128, 64), [{"tone": (0, 255)}])
        darker = orig.to_grey(grey, (128, 64), [{"tone": (0, 255), "gamma": 2.0}])
        self.assertEqual((plain[0, 0], darker[0, 0]), (2, 1))

    def test_layer_applies_from_its_edge(self):
        grey = self.flat_image((256, 64), (128, 128, 128))
        levels = orig.to_grey(grey, (128, 64), [{"tone": (0, 255)}, {"from": 160, "tone": (0, 128)}])
        self.assertTrue((levels[:, :80] == 2).all())
        self.assertTrue((levels[:, 80:] == 3).all())

    def test_hue_subject_gets_own_tone_and_black_outline(self):
        img = self.flat_image((256, 128), (100, 0, 0))
        img.paste((0, 0, 100), (96, 32, 160, 96))            # blaues Quadrat
        layer = {"weights": (0.5, 0, 0.5), "tone": (0, 100)}
        subject = {"mask": ("hue", "blue"), "weights": (0, 0, 1), "tone": (0, 100), "min": 1, "outline": True}
        levels = orig.to_grey(img, (128, 64), [layer], [subject])
        self.assertEqual(levels[32, 64], 3)                  # Motiv: eigene Tonkurve
        self.assertEqual(levels[32, 47], 0)                  # Rand links vom Motiv
        self.assertEqual(levels[32, 20], 2)                  # Hintergrund unverändert
        self.assertTrue((levels[16:48, 48:80] >= 1).all())

    def test_polygon_subject_with_min_on_dark_ground(self):
        # dunkler Verlauf 0–40: ohne Freistellung überall schwarz
        dark = Image.linear_gradient("L").resize((256, 128)).point(lambda v: v * 40 // 255).convert("RGB")
        subject = {"mask": ("polygon", [(64, 32), (192, 32), (192, 96), (64, 96)]), "tone": "auto", "min": 1,
                   "outline": True}
        levels = orig.to_grey(dark, (128, 64), [{"tone": (0, 255)}], [subject])
        self.assertTrue((levels[16:48, 32:96] >= 1).all())   # Motiv mindestens dunkelgrau
        self.assertEqual(levels[16:48, 32:96].max(), 3)      # tone auto: Umfang des Motivs
        self.assertTrue((levels[15, 32:96] == 0).all())      # Rand
        self.assertEqual(levels[0, 0], 0)

    def test_auto_tone_needs_some_range(self):
        dark = self.flat_image((256, 128), (10, 10, 10))
        subject = {"mask": ("polygon", [(64, 32), (192, 32), (192, 96)]), "tone": "auto"}
        with self.assertRaisesRegex(ValueError, "überall gleich hell"):
            orig.to_grey(dark, (128, 64), [{"tone": (0, 255)}], [subject])

    def test_planes_are_levels_above_their_index(self):
        levels = np.array([[0, 1, 2, 3]], dtype=np.uint8)
        planes = [np.asarray(p)[..., 0] > 0 for p in orig.grey_planes(levels)]
        self.assertEqual([p[0].tolist() for p in planes],
                         [[False, True, True, True], [False, False, True, True], [False, False, False, True]])

    def test_card_text_is_white_and_ground_black(self):
        bright = np.zeros((16, 16))
        bright[4:12, 4:12] = 200
        levels = orig.card_grey(bright, 50)
        self.assertEqual((levels[0, 0], levels[8, 8]), (0, 3))

    def test_needs_a_layer(self):
        with self.assertRaises(ValueError):
            orig.to_grey(self.flat_image((256, 64), (0, 0, 0)), (128, 64), [])


class WalkboxTest(unittest.TestCase):
    def test_touching_boxes_are_neighbours(self):
        line = [(0, 10), (10, 10), (10, 10), (0, 10)]         # waagerechte Linie
        square = [(10, 5), (20, 5), (20, 15), (10, 15)]      # berührt deren Ende
        far = [(40, 0), (50, 0), (50, 5), (40, 5)]
        self.assertEqual(advc.box_distance(line, square), 0.0)
        self.assertGreater(advc.box_distance(square, far), advc.BOX_TOUCH)

    def test_overlapping_boxes_are_neighbours(self):
        a = [(0, 0), (10, 0), (10, 10), (0, 10)]
        b = [(2, 2), (4, 2), (4, 4), (2, 4)]                 # liegt ganz in a
        self.assertEqual(advc.box_distance(a, b), 0.0)

    def test_matrix_routes_through_chain(self):
        # 0 – 1 – 2 in einer Kette, 3 isoliert
        quads = [[(0, 0), (10, 0), (10, 10), (0, 10)],
                 [(10, 0), (20, 0), (20, 10), (10, 10)],
                 [(20, 0), (30, 0), (30, 10), (20, 10)],
                 [(60, 0), (70, 0), (70, 10), (60, 10)]]
        m = advc.box_matrix(quads)
        self.assertEqual(m[0], [0, 1, 1, advc.NONE8])
        self.assertEqual(m[2][0], 1)
        self.assertEqual(m[3], [advc.NONE8, advc.NONE8, advc.NONE8, 3])

    def test_rect_walkbox_emits_matrix(self):
        _, data = compile_source(MINIMAL)
        room = read_record(data, "RoomRec", header(data)["rooms"])
        self.assertEqual(room["boxCount"], 1)
        self.assertEqual(data[room["matrix"]], 0)
        box = read_record(data, "BoxRec", room["boxes"])
        self.assertEqual((box["ulx"], box["uly"], box["lrx"], box["lry"]), (0, 40, 127, 63))

    def test_without_greyscale_everything_is_one_bit(self):
        _, data = compile_source(MINIMAL)
        hdr = header(data)
        room = read_record(data, "RoomRec", hdr["rooms"])
        place = read_record(data, "PlaceRec", room["places"])
        self.assertEqual((hdr["titleGrey"], room["grey"], place["grey"]), (advc.NONE24,) * 3)
        self.assertEqual(cstring(data, hdr["uiGreyOn"]), b"Greyscale: on")
        self.assertEqual(cstring(data, hdr["uiGreyOff"]), b"Greyscale: off")

    def test_grey_image_has_three_planes_per_frame(self):
        comp, _ = compile_source(MINIMAL)
        a = np.array([[0, 1], [2, 3]], dtype=np.uint8).repeat(4, axis=0)   # 2 × 8
        b = 3 - a
        comp.grey_image("test_grey", [a, b], None, mask=True)
        img = comp.images[("test_grey", True)]
        self.assertEqual((img["w"], img["h"], img["frames"]), (2, 8, 6))
        data = img["data"][4:]
        for f, levels in enumerate((a, b)):
            for p in range(3):
                frame = 3 * f + p
                for x in range(2):
                    byte, mask = data[(frame * 2 + x) * 2], data[(frame * 2 + x) * 2 + 1]
                    self.assertEqual(mask, 0xFF)
                    want = sum(1 << y for y in range(8) if levels[y, x] > p)
                    self.assertEqual(byte, want, f"Frame {frame}, Spalte {x}")

    def test_greyscale_errors(self):
        cases = {
            "greyscale room start\n  layer gamma 1.3\nend": "nur für Räume aus den Originaldaten",
            "greyscale room nowhere\n  layer\nend": "Raum 'nowhere' unbekannt",
            "greyscale card part9\n  layer\nend": "Karte 'part9' unbekannt",
            "greyscale title\n  layer\nend": "nur für title original",
            "greyscale room start\nend": "mindestens eine layer-Zeile",
            "greyscale room start\n  layer shine 2\nend": "unbekannte Option 'shine'",
            "greyscale room start\n  layer from 10\nend": "ab dem linken Rand",
            "greyscale room start\n  layer\n  layer\nend": "dieselben? Kante|derselben Kante",
            "greyscale room start\n  layer channel max weights 1 0 0\nend": "schließen sich aus",
            "greyscale room start\n  layer tone 90 20\nend": "schwarz < weiß",
            "greyscale room start\n  layer max 4\nend": "max: 0…3",
            "greyscale room start\n  layer min 1\nend": "min nur bei subject",
            "greyscale room start\n  layer\n  subject outline\nend": "hue <name> oder polygon",
            "greyscale room start\n  layer\n  subject hue green\nend": "hue: blue/magenta",
            "greyscale room start\n  layer\n  subject polygon 1 2 3 4\nend": "mindestens drei Punkte",
            "greyscale room start\n  layer\n  subject polygon 0 0 9 0 9 9 dither\nend": "unbekannte Option 'dither'",
            "greyscale room start\n  layer\n": "'end' fehlt",
            "greyscale\n  layer\nend": "Syntax: greyscale",
        }
        for block, message in cases.items():
            with self.subTest(block=block.splitlines()[0]):
                with self.assertRaisesRegex(advc.CompileError, message):
                    compile_source(MINIMAL + block)

    def test_greyscale_block_only_once(self):
        block = "greyscale room start\n  layer\nend\n"
        with self.assertRaisesRegex(advc.CompileError, "doppelt"):
            compile_source(MINIMAL + block + block)

    def test_original_needs_data_dir(self):
        src = MINIMAL.replace('room start bg "bg.png"', "room start original 38")
        with self.assertRaisesRegex(advc.CompileError, "braucht die Originaldaten"):
            compile_source(src)


def original_dir():
    """Verzeichnis der eigenen Originalkopie: MI_ORIGINAL, sonst die erste
    Kopie aus ORIGINALS in config.mk (README) oder das ältere ORIGINAL."""
    if os.environ.get("MI_ORIGINAL"):
        return Path(os.environ["MI_ORIGINAL"])
    config = ROOT / "config.mk"
    if config.exists():
        m = re.search(r"^ORIGINALS?\s*:?=\s*(\S+)", config.read_text(), re.M)
        if m:
            return Path(m.group(1).strip())
    return None


ORIGINAL = original_dir()
HAVE_ORIGINAL = ORIGINAL is not None and (ORIGINAL / "DISK01.LEC").exists()


@unittest.skipUnless(HAVE_ORIGINAL, "Originaldaten nicht vorhanden (MI_ORIGINAL oder config.mk)")
class OriginalDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import scumm_v4
        cls.rooms = scumm_v4.read_rooms(ORIGINAL)

    def test_lookout_room(self):
        r = self.rooms[38]
        self.assertEqual((r.width, r.height, len(r.boxes)), (320, 144, 5))
        self.assertEqual({o.name for o in r.objects if o.width}, {"stairs", "path", "lookout"})
        img = r.image()
        self.assertEqual(img.size, (320, 144))
        # Nachthimmel: dunkel und blau
        red, green, blue = img.getpixel((40, 20))
        self.assertLess(red + green, blue * 2)

    def test_every_room_decodes(self):
        for n, r in self.rooms.items():
            if r.height:
                with self.subTest(room=n):
                    self.assertEqual(r.image().size, (r.width, r.height))

    def test_guybrush_costume(self):
        import scumm_v4 as sv
        import original as orig
        costume = sv.read_costume(ORIGINAL, 1)
        walk = [sv.INIT, sv.STAND, sv.WALK]
        self.assertEqual(orig.cycle_length(costume, walk, sv.RIGHT), 6)
        palette = self.rooms[38].palette
        strip, fw, fh = orig.costume_frames(
            costume, palette, [([sv.INIT, sv.STAND], sv.RIGHT, 0)], 0.55)
        self.assertEqual(strip.size, (fw, fh))
        self.assertTrue(20 <= fh <= 32, fh)   # Guybrush ~48 px im Original
        # Silhouette: nur Weiß, Schwarz und Transparent
        colors = {px for px in strip.getdata()}
        self.assertTrue(colors <= {(255, 255, 255, 255), (0, 0, 0, 255), (0, 0, 0, 0)})

    def test_title_logo(self):
        logo = self.rooms[10].object_image(113)
        self.assertEqual(logo.size, (216, 120))

    def test_game_compiles(self):
        game = advc.Game(ROOT / "game")
        game.original_dir = ORIGINAL
        game.languages = [textsource.TextSource(ORIGINAL)]
        advc.Parser(game, advc.tokenize((ROOT / "game" / "game.adv").read_text(encoding="utf-8"))).parse()
        comp = advc.Compiler(game)
        data = comp.compile()
        self.assertGreater(len(data), 1000)

        # Graustufen: jeder Raum aus dem Original, jedes Bild aus der Kulisse
        # (Türen, Gegenstände) oder aus einem Kostüm, jede Figur, Titel und
        # Kapitelkarte
        hdr = header(data)
        self.assertNotEqual(hdr["titleGrey"], advc.NONE24)
        size = advc.record_size("RoomRec")
        for i, (rid, r) in enumerate(game.rooms.items()):
            room = read_record(data, "RoomRec", hdr["rooms"] + i * size)
            with self.subTest(room=rid):
                self.assertEqual(room["grey"] == advc.NONE24, bool(r.get("blank")))
                for k, p in enumerate(r["places"]):
                    place = read_record(data, "PlaceRec", room["places"] + k * advc.record_size("PlaceRec"))
                    has_grey = p["grey_from"] is not None or p["grey_sprite"] is not None
                    self.assertEqual(place["grey"] != advc.NONE24, has_grey, p["object"])
        self.assertIn((f"card_part1_{game.languages[0].code}_grey", False), comp.images)
        for i, aid in enumerate(game.actors):
            actor = read_record(data, "ActorRec", hdr["actors"] + i * advc.record_size("ActorRec"))
            with self.subTest(actor=aid):
                self.assertEqual(actor["grey"] != advc.NONE24, "grey" in game.actors[aid])

        # backdrop: Zustand 0 einer Tür ist genau der Ausschnitt der Kulisse –
        # sonst ließe die Engine (die ihn nicht zeichnet) etwas weg.
        doors = 0
        for i, (rid, r) in enumerate(game.rooms.items()):
            room = read_record(data, "RoomRec", hdr["rooms"] + i * size)
            for k in range(len(r["places"])):
                place = read_record(data, "PlaceRec", room["places"] + k * advc.record_size("PlaceRec"))
                if not place["backdrop"]:
                    continue
                doors += 1
                x, y = place["x"], place["y"]
                for frame in range(4):   # 1 Bit, dann die drei Graustufen-Ebenen
                    bg = bitmap(data, room["background"] if frame == 0 else room["grey"], max(0, frame - 1))
                    img = bitmap(data, place["image"] if frame == 0 else place["grey"], max(0, frame - 1), masked=True)
                    cut = [row[x:x + len(img[0])] for row in bg[y:y + len(img)]]
                    with self.subTest(room=rid, place=k, frame=frame):
                        self.assertEqual(img, cut)
        self.assertGreater(doors, 5)


if __name__ == "__main__":
    unittest.main()
