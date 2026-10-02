"""Aufbereitung der Original-Grafiken für das 128×64-Schwarz-Weiß-Display.

Die Hintergründe von Monkey Island sind dunkle 256-Farben-Bilder (der
Nachthimmel des Aussichtspunkts liegt bei einer Helligkeit um 20 von 255,
die hellsten Steine um 100). Eine einfache Kontrastanpassung rastert darum
den ganzen Himmel zu grauem Rauschen. Stattdessen:

  1. Flächenmittel auf Zielgröße verkleinern (BOX-Filter),
  2. Helligkeit mit warmer Gewichtung (Stein und Feuer hell, blaue Nacht
     dunkel),
  3. lokalen Kontrast verstärken (Abstand zum weichgezeichneten Bild),
  4. Tonwertbereich [schwarz, weiß] pro Raum auf 0–255 ziehen,
  5. Atkinson-Rasterung: Sie verteilt nur 6/8 des Fehlers und lässt große
     dunkle Flächen dadurch sauber schwarz statt verrauscht.
"""
import numpy as np
from PIL import Image, ImageFilter

WARM = (0.5, 0.4, 0.1)
_ATKINSON = ((1, 0), (2, 0), (-1, 1), (0, 1), (1, 1), (0, 2))


def _luminance(img):
    a = np.asarray(img.convert("RGB"), dtype=float)
    return a[..., 0] * WARM[0] + a[..., 1] * WARM[1] + a[..., 2] * WARM[2]


def _atkinson(values):
    a = values.copy()
    h, w = a.shape
    out = np.zeros((h, w), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            old = a[y, x]
            new = 255 if old >= 128 else 0
            out[y, x] = new
            err = (old - new) / 8
            for dx, dy in _ATKINSON:
                if 0 <= x + dx < w and 0 <= y + dy < h:
                    a[y + dy, x + dx] += err
    return out


# Woraus die Helligkeit kommt: gewichtete Luminanz oder ein Farbkanal. Ein
# Kanal trennt Flächen gleicher Helligkeit, aber verschiedener Farbe, etwa
# das blaue Geisterschiff vor der roten Lava (Raum 70).
CHANNELS = ("luminance", "red", "green", "blue", "max")


def _brightness(img, channel):
    if channel == "luminance":
        return _luminance(img)
    a = np.asarray(img.convert("RGB"), dtype=float)
    if channel == "max":
        return a.max(axis=2)
    return a[..., ("red", "green", "blue").index(channel)]


def to_mono(img, size, black, white, contrast, channel="luminance"):
    """Farbbild → deckendes Schwarz-Weiß-Bild (RGBA) in der Zielgröße."""
    if not 0 <= black < white <= 255:
        raise ValueError(f"Tonwerte: 0 ≤ schwarz < weiß ≤ 255, nicht {black}/{white}")
    if channel not in CHANNELS:
        raise ValueError(f"Kanal: {'/'.join(CHANNELS)}, nicht {channel}")
    small = img.convert("RGB").resize(size, Image.BOX)
    lum = _brightness(small, channel)
    blurred = _brightness(small.filter(ImageFilter.GaussianBlur(3)), channel)
    lum = lum + contrast * (lum - blurred)
    levels = np.clip((lum - black) / (white - black), 0, 1) * 255
    mono = _atkinson(levels)
    rgba = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = mono
    rgba[..., 3] = 255
    return Image.fromarray(rgba, "RGBA")


def scaled(value, factor):
    return int(round(value * factor))


# SCUMM-v4-Blickrichtungen (0 West, 1 Ost, 2 Süd, 3 Nord) → Engine
# (0 rechts, 1 links, 2 frontal). Nach hinten gibt es keine Frames, also frontal.
SCUMM_DIRS = {0: 1, 1: 0, 2: 2, 3: 2}


# --------------------------------------------------------------------------
# Figuren aus Original-Kostümen
# --------------------------------------------------------------------------
#
# Bei 1/2 Originalgröße bleiben von einer Figur etwa 10×26 Pixel. Jede Form
# von Rasterung macht daraus einen Fleck. Lesbar bleibt eine helle Silhouette
# in der Originalform, in der nur die dunkelsten Partien (Hose, Haare,
# Mantel) schwarz sind, mit schwarzem Rand gegen die gerasterte Kulisse.

# Render-Leinwand für ein Kostüm: groß genug für alle Figuren des Spiels,
# Fußpunkt unten mittig.
_CANVAS = (240, 240)
_ORIGIN = (120, 200)


def _silhouette(img, dark, outline):
    """Skaliertes RGBA → weiße Silhouette, sehr dunkle Pixel schwarz, Rand."""
    a = np.asarray(img, dtype=float)
    alpha = a[..., 3] > 110
    lum = a[..., 0] * 0.35 + a[..., 1] * 0.5 + a[..., 2] * 0.15
    h, w = alpha.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[alpha] = (255, 255, 255, 255)
    out[alpha & (lum < dark)] = (0, 0, 0, 255)
    opaque = alpha.copy()
    for step in range(outline):
        # Rand wächst erst um die weißen Pixel, dann um alles Deckende.
        src = (out[..., 0] == 255) & opaque if step == 0 else opaque
        grown = src.copy()
        grown[1:, :] |= src[:-1, :]
        grown[:-1, :] |= src[1:, :]
        grown[:, 1:] |= src[:, :-1]
        grown[:, :-1] |= src[:, 1:]
        new = grown & ~opaque
        out[new] = (0, 0, 0, 255)
        opaque |= new
    return Image.fromarray(out, "RGBA")


def costume_frames(costume, palette, sequences, scale, dark=45, outline=1):
    """Rendert Kostüm-Frames und bringt sie auf eine gemeinsame Framegröße.

    sequences: Liste von (Aktionen, Richtung, Schritte) – der Frame zeigt den
    Zustand nach Anwenden der Aktionen und Schritte-mal Weiterschalten.
    Rückgabe: (Streifen als RGBA, Framebreite, Framehöhe). Der Fußpunkt liegt
    unten mittig im Frame, so wie die Engine Actors zeichnet.
    """
    raw = []
    for actions, direction, steps in sequences:
        state = costume.new_state()
        for action in actions:
            costume.apply(state, action, direction)
        for _ in range(steps):
            costume.advance(state)
        raw.append(costume.render(state, palette, _CANVAS, _ORIGIN))
    boxes = [im.getbbox() for im in raw if im.getbbox()]
    if not boxes:
        raise ValueError(f"Kostüm {costume.number}: keine sichtbaren Pixel")
    ox, oy = _ORIGIN
    half = max(max(ox - b[0], b[2] - ox) for b in boxes)
    top = min(b[1] for b in boxes)
    crop = (ox - half, top, ox + half, oy + 1)   # unten endet der Frame am Fuß
    w = max(1, scaled(crop[2] - crop[0], scale))
    h = max(1, scaled(crop[3] - crop[1], scale))
    fw, fh = w + 2 * outline, h + outline        # Rand links, rechts, oben
    strip = Image.new("RGBA", (fw * len(raw), fh), (0, 0, 0, 0))
    for i, im in enumerate(raw):
        small = im.crop(crop).resize((w, h), Image.BOX)
        padded = Image.new("RGBA", (fw, fh + outline), (0, 0, 0, 0))
        padded.paste(small, (outline, outline))
        mono = _silhouette(padded, dark, outline).crop((0, 0, fw, fh))
        strip.paste(mono, (i * fw, 0))
    return strip, fw, fh


def cycle_length(costume, actions, direction, limit=16):
    """Anzahl der Schritte, bis sich eine Animation wiederholt."""
    state = costume.new_state()
    for action in actions:
        costume.apply(state, action, direction)
    first = list(state["pos"])
    for n in range(1, limit + 1):
        costume.advance(state)
        if state["pos"] == first:
            return n
    return limit
