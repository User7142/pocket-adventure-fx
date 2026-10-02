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


# --------------------------------------------------------------------------
# Graustufen (2 Bit) für ArduboyG
# --------------------------------------------------------------------------
#
# Das Display bleibt schwarz-weiß; ArduboyG zeigt drei Bildebenen so schnell
# nacheinander, dass ein Pixel je nach Zahl seiner hellen Ebenen schwarz,
# dunkelgrau, hellgrau oder weiß wirkt (Stufe 0–3, Ebene p = Stufe > p).
#
# Auf dem OLED wirken Grautöne deutlich heller als ihre Zahlenwerte. Was in
# einer Vorschau ausgewogen aussieht, ist auf dem Gerät flau; die Werte in
# game.adv sind darum am Gerät ausgesucht. Bewährt haben sich:
#
#   - flache Stufen statt Rasterung: Raster flimmern bei drei Ebenen sichtbar,
#   - etwas Gamma (> 1), das die Mitten abdunkelt,
#   - ein Motiv, das im Grau untergeht (das Geisterschiff in der Lava, das
#     Logo vor dem Himmel), freistellen: eigene, hellere Tonkurve und ein
#     schwarzer Rand von 1 px.
#
# Beschreibung eines Bildes (aus einem greyscale-Block in game.adv):
#   layers:   Liste von Schichten (dict), jede gilt ab ihrer Kante "from"
#             (x im Original, 0 = ganzes Bild) bis zur nächsten,
#   subjects: Liste von Motiven (dict) mit "mask" ("hue", Name) oder
#             ("polygon", [(x, y), …]) im Original, dazu Tonoptionen,
#             "min" (dunkelste Stufe) und "outline".
# Tonoptionen: "channel" oder "weights" (r, g, b), "tone" (schwarz, weiß)
# oder "auto", "contrast", "gamma", "max" (hellste Stufe), "dither".

GREY_LEVELS = 4
GREY_PLANES = GREY_LEVELS - 1
# Automatischer Tonbereich: Perzentile der Helligkeit im betroffenen Bereich
AUTO_TONE = (3, 99.5)

# Farbmasken für Motive, auf dem Originalbild (Werte 0–255 je Kanal)
_HUES = {
    # Geisterschiff: blau vor roter Lava
    "blue": lambda r, g, b: b > r + 15,
    # Logo von Monkey Island: magenta vor blauem Himmel
    "magenta": lambda r, g, b: (r > 90) & (b > 70) & (g < r * 0.6),
}
HUES = tuple(_HUES)
# Anteil des Motivs an einem Zielpixel, ab dem es zum Motiv zählt
_HUE_COVER = 0.4
_POLYGON_COVER = 0.5


def _value(small, opts, default_channel):
    """Helligkeit 0–255 je Pixel aus einem verkleinerten RGB-Bild."""
    if "weights" in opts:
        return np.asarray(small, dtype=float) @ np.array(opts["weights"], dtype=float)
    return _brightness(small, opts.get("channel", default_channel))


def _graded(small, opts, defaults, area):
    """Tonkurve einer Schicht oder eines Motivs: Werte 0–1 je Pixel. area
    (bool-Maske) ist der Bezug für tone auto: bei Schichten das ganze Bild
    (alle Schichten gleich belichtet), bei Motiven das Motiv selbst (es soll
    sich abheben)."""
    channel = defaults["channel"]
    v = _value(small, opts, channel)
    contrast = opts.get("contrast", 0.0)
    if contrast:
        blurred = _value(small.filter(ImageFilter.GaussianBlur(3)), opts, channel)
        v = v + contrast * (v - blurred)
    tone = opts.get("tone", defaults["tone"])
    if tone == "auto":
        if not area.any():
            raise ValueError("tone auto: der Bereich ist leer")
        tone = tuple(np.percentile(v[area], AUTO_TONE))
        if tone[1] <= tone[0]:
            raise ValueError(f"tone auto: der Bereich ist überall gleich hell ({tone[0]:.0f}); "
                             "tone S W angeben")
    black, white = tone
    if white <= black:
        raise ValueError(f"Tonwerte: schwarz < weiß, nicht {black}/{white}")
    return np.clip((v - black) / (white - black), 0, 1) ** opts.get("gamma", 1.0)


def _quantize(t, lo, hi, dither):
    """Werte 0–1 → Stufen lo..hi; flach oder mit Atkinson-Rasterung."""
    if not dither:
        return lo + np.rint(t * (hi - lo)).astype(np.uint8)
    a = t * (hi - lo)
    h, w = a.shape
    out = np.zeros((h, w), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            q = int(np.clip(round(a[y, x]), 0, hi - lo))
            out[y, x] = lo + q
            err = (a[y, x] - q) / 8
            for dx, dy in _ATKINSON:
                if 0 <= x + dx < w and 0 <= y + dy < h:
                    a[y + dy, x + dx] += err
    return out


def _grow(m):
    g = m.copy()
    g[1:, :] |= m[:-1, :]
    g[:-1, :] |= m[1:, :]
    g[:, 1:] |= m[:, :-1]
    g[:, :-1] |= m[:, 1:]
    return g


def _subject_mask(img, size, mask):
    kind, arg = mask
    if kind == "hue":
        a = np.asarray(img.convert("RGB"), dtype=float)
        on = _HUES[arg](a[..., 0], a[..., 1], a[..., 2])
        cover = Image.fromarray((on * 255).astype(np.uint8), "L").resize(size, Image.BOX)
        return np.asarray(cover, dtype=float) / 255 > _HUE_COVER
    from PIL import ImageDraw
    full = Image.new("L", img.size, 0)
    ImageDraw.Draw(full).polygon(arg, fill=255)
    return np.asarray(full.resize(size, Image.BOX), dtype=float) / 255 > _POLYGON_COVER


def to_grey(img, size, layers, subjects=(), tone=(30, 95), channel="luminance"):
    """Farbbild → Graustufen (np.uint8, Stufen 0–3) in der Zielgröße.
    tone und channel sind die Vorgaben des Raums für Schichten ohne eigene."""
    if not layers:
        raise ValueError("Graustufen brauchen mindestens eine Schicht (layer)")
    defaults = {"tone": tone, "channel": channel}
    small = img.convert("RGB").resize(size, Image.BOX)
    w, h = size
    sx = w / img.width
    out = np.zeros((h, w), dtype=np.uint8)
    edges = sorted(layers, key=lambda layer: layer.get("from", 0))
    for i, layer in enumerate(edges):
        x0 = round(layer.get("from", 0) * sx)
        x1 = round(edges[i + 1]["from"] * sx) if i + 1 < len(edges) else w
        whole = np.ones((h, w), dtype=bool)
        levels = _quantize(_graded(small, layer, defaults, whole), 0, layer.get("max", GREY_PLANES),
                           layer.get("dither", False))
        out[:, x0:x1] = levels[:, x0:x1]
    for subject in subjects:
        m = _subject_mask(img, size, subject["mask"])
        if any(k in subject for k in ("tone", "channel", "weights", "contrast", "gamma")):
            levels = _quantize(_graded(small, subject, defaults, m), subject.get("min", 0),
                               subject.get("max", GREY_PLANES), False)
            out[m] = levels[m]
        if subject.get("outline"):
            out[_grow(m) & ~m] = 0
    return out


def card_grey(bright, threshold, gamma=1.0, dither=False):
    """Kapitelkarte (Helligkeit des hellsten Kanals, schon zugeschnitten und
    verkleinert) → Stufen 0–3. Die Karte ist Schrift: schwarz ab der halben
    Schwelle, weiß bei der typischen Helligkeit der Schrift selbst."""
    text = bright[bright > threshold]
    white = np.percentile(text, 60) if text.size else 255
    t = np.clip((bright - threshold / 2) / max(1.0, white - threshold / 2), 0, 1) ** gamma
    return _quantize(t, 0, GREY_PLANES, dither)


def grey_planes(levels, opaque=None):
    """Stufen → die drei Ebenen als Schwarz-Weiß-Bilder (RGBA); opaque
    (bool-Maske) macht die übrigen Pixel durchsichtig (Figuren)."""
    planes = []
    for p in range(GREY_PLANES):
        on = (levels > p).astype(np.uint8) * 255
        rgba = np.zeros(levels.shape + (4,), dtype=np.uint8)
        rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = on
        rgba[..., 3] = 255 if opaque is None else np.where(opaque, 255, 0)
        planes.append(Image.fromarray(rgba, "RGBA"))
    return planes


def grey_preview(levels):
    """Stufen → Graustufenbild zum Ansehen (0, 85, 170, 255)."""
    return Image.fromarray((levels * (255 // GREY_PLANES)).astype(np.uint8), "L")


def figure_levels(strip, fw):
    """Streifen aus _figure_grey → je Frame (Stufen, deckend)."""
    a = np.asarray(strip)
    return [(a[:, x:x + fw, 0] // (255 // GREY_PLANES), a[:, x:x + fw, 3] > 0)
            for x in range(0, strip.width, fw)]


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


def _figure_lum(a):
    """Helligkeit der Figurenpixel (wie _silhouette)."""
    return a[..., 0] * 0.35 + a[..., 1] * 0.5 + a[..., 2] * 0.15


def _figure_grey(img, dark, outline, tone):
    """Skaliertes RGBA → Figur in Graustufen (RGBA, Grau 0/85/170/255):
    dieselbe Form und derselbe Rand wie _silhouette, aber statt einer weißen
    Fläche die Helligkeiten des Kostüms. Sehr dunkle Pixel bleiben schwarz,
    die übrigen verteilen sich über tone (schwarz, weiß) auf die Stufen 1–3 –
    eine Figur bleibt so nie dunkler als Dunkelgrau und hebt sich mit dem
    schwarzen Rand von jeder Kulisse ab."""
    a = np.asarray(img, dtype=float)
    alpha = a[..., 3] > 110
    lum = _figure_lum(a)
    t = np.clip((lum - tone[0]) / max(1.0, tone[1] - tone[0]), 0, 1)
    levels = np.zeros(alpha.shape, dtype=np.uint8)
    levels[alpha] = 1 + np.rint(t[alpha] * 2).astype(np.uint8)
    levels[alpha & (lum < dark)] = 0
    opaque = alpha.copy()
    for step in range(outline):
        src = (levels > 0) & opaque if step == 0 else opaque
        grown = src.copy()
        grown[1:, :] |= src[:-1, :]
        grown[:-1, :] |= src[1:, :]
        grown[:, 1:] |= src[:, :-1]
        grown[:, :-1] |= src[:, 1:]
        new = grown & ~opaque
        levels[new] = 0
        opaque |= new
    out = np.zeros(alpha.shape + (4,), dtype=np.uint8)
    out[..., 0] = out[..., 1] = out[..., 2] = levels * (255 // GREY_PLANES)
    out[..., 3] = np.where(opaque, 255, 0)
    return Image.fromarray(out, "RGBA")


# Tonbereich einer Figur in Graustufen: Perzentile der Helligkeit über alle
# Frames eines Kostüms (ohne die schwarzen Partien) – gemeinsam, damit eine
# Figur beim Laufen nicht die Helligkeit wechselt.
FIGURE_TONE = (10, 95)


def costume_frames(costume, palette, sequences, scale, dark=45, outline=1, grey=False):
    """Rendert Kostüm-Frames und bringt sie auf eine gemeinsame Framegröße.

    sequences: Liste von (Aktionen, Richtung, Schritte) – der Frame zeigt den
    Zustand nach Anwenden der Aktionen und Schritte-mal Weiterschalten.
    Rückgabe: (Streifen als RGBA, Framebreite, Framehöhe). Der Fußpunkt liegt
    unten mittig im Frame, so wie die Engine Actors zeichnet. Mit grey=True
    zusätzlich als vierter Wert derselbe Streifen in Graustufen
    (_figure_grey: Grau 0/85/170/255, Alpha 0/255).
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
    padded_frames = []
    for i, im in enumerate(raw):
        small = im.crop(crop).resize((w, h), Image.BOX)
        padded = Image.new("RGBA", (fw, fh + outline), (0, 0, 0, 0))
        padded.paste(small, (outline, outline))
        padded_frames.append(padded)
        mono = _silhouette(padded, dark, outline).crop((0, 0, fw, fh))
        strip.paste(mono, (i * fw, 0))
    if not grey:
        return strip, fw, fh
    lit = []
    for padded in padded_frames:
        a = np.asarray(padded, dtype=float)
        lum = _figure_lum(a)
        lit.append(lum[(a[..., 3] > 110) & (lum >= dark)])
    lit = np.concatenate(lit)
    tone = tuple(np.percentile(lit, FIGURE_TONE)) if lit.size else (dark, 255)
    grey_strip = Image.new("RGBA", strip.size, (0, 0, 0, 0))
    for i, padded in enumerate(padded_frames):
        grey_strip.paste(_figure_grey(padded, dark, outline, tone).crop((0, 0, fw, fh)), (i * fw, 0))
    return strip, fw, fh, grey_strip


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
