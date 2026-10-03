"""Text reader for SCUMM v4 (tools/scumm_text.py).

The reader must skip every command with its parameters exactly, otherwise
the text numbers shift. The yardstick is descumm (scummvm-tools): for every
script block of a copy, both must find the same strings in the same
order. Needs a copy of the game (ORIGINAL or ORIGINAL_DE in the
environment or config.mk) and descumm on the path, otherwise skipped.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import scumm_text  # noqa: E402


def _config(name):
    if os.environ.get(name):
        return os.environ[name]
    cfg = ROOT / "config.mk"
    if cfg.exists():
        m = re.search(rf"^{name}\s*:?=\s*(.+)$", cfg.read_text(), re.M)
        if m:
            return m.group(1).strip()
    return None


COPIES = {lang: _config(var) for lang, var in (("en", "ORIGINAL"), ("de", "ORIGINAL_DE"))}

# descumm shows text as "…"; \xNN and \" or \\ are escapes.
_QUOTED = re.compile(rb'"((?:[^"\\]|\\.)*)"')


def _unescape(raw):
    out, i = bytearray(), 0
    while i < len(raw):
        if raw[i:i + 2] == b"\\x":
            out.append(int(raw[i + 2:i + 4], 16))
            i += 4
        elif raw[i:i + 1] == b"\\":
            out.append(raw[i + 1])
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return bytes(out)


def descumm_runs(block, workdir):
    path = Path(workdir) / "block"
    path.write_bytes(block)
    out = subprocess.run(["descumm", "-4", str(path)], capture_output=True, check=True).stdout
    return [_unescape(m.group(1)) for m in _QUOTED.finditer(out)]


def own_runs(block, ident):
    return [p.encode("cp437") for t in scumm_text.decode_block(block, ident)
            for p in t.parts if isinstance(p, str)]


@unittest.skipUnless(shutil.which("descumm"), "descumm (scummvm-tools) not installed")
class AgainstDescumm(unittest.TestCase):
    def check_copy(self, lang):
        game_dir = COPIES[lang]
        if not game_dir or not Path(game_dir, "000.LFL").exists():
            self.skipTest(f"no copy of the original game for {lang}")
        with tempfile.TemporaryDirectory() as work:
            count = 0
            for ident, block in scumm_text.iter_blocks(game_dir):
                with self.subTest(block=ident):
                    self.assertEqual(own_runs(block, ident), descumm_runs(block, work))
                count += 1
        self.assertGreater(count, 1000)

    def test_english(self):
        self.check_copy("en")

    def test_german(self):
        self.check_copy("de")


class TextObject(unittest.TestCase):
    def test_plain_turns_codes_into_breaks(self):
        t = scumm_text.Text("x#1", "print", ["Hallo", (1, None), "Welt", (3, None), "!"], 0)
        self.assertEqual(t.plain(), "Hallo\nWelt\f!")

    def test_plain_option_break_and_string_variable(self):
        # Code 8 wraps long dialogue options; code 7 inserts a
        # string variable, provided the engine knows it (number → value).
        t = scumm_text.Text("x#1", "verb", ["Lang ", (8, 0x2020), "er"], 0)
        self.assertEqual(t.plain(), "Lang \ner")
        t = scumm_text.Text("x#1", "print", ["Hallo ", (7, 30), "."], 0)
        self.assertEqual(t.plain({30: 1}), "Hallo \x01" + chr(scumm_text.SLOT_BASE + 1) + ".")
        with self.assertRaises(scumm_text.ScummError):
            t.plain({31: 1})

    def test_plain_rejects_variables(self):
        t = scumm_text.Text("x#1", "print", ["Ich bin ", (6, 1)], 0)
        with self.assertRaises(scumm_text.ScummError):
            t.plain()


if __name__ == "__main__":
    unittest.main()
