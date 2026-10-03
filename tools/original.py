"""Preparation of the original graphics for the 128×64 black-and-white display.

The Monkey Island backgrounds are dark 256-colour images (the night sky
of the lookout point sits at a brightness around 20 of 255, the brightest
stones around 100). A simple contrast adjustment therefore dithers the
whole sky into grey noise. Instead:

  1. downscale to the target size by area average (BOX filter),
  2. brightness with warm weighting (stone and fire bright, blue night
     dark),
  3. boost local contrast (difference to the blurred image),
  4. stretch the tone range [black, white] per room to 0–255,
  5. Atkinson dithering: it spreads only 6/8 of the error and thus keeps
     large dark areas cleanly black instead of noisy.
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


# Where the brightness comes from: weighted luminance or a colour channel. A
# channel separates areas of equal brightness but different colour, e.g.
# the blue ghost ship in front of the red lava (room 70).
CHANNELS = ("luminance", "red", "green", "blue", "max")


def _brightness(img, channel):
    if channel == "luminance":
        return _luminance(img)
    a = np.asarray(img.convert("RGB"), dtype=float)
    if channel == "max":
        return a.max(axis=2)
    return a[..., ("red", "green", "blue").index(channel)]


def to_mono(img, size, black, white, contrast, channel="luminance"):
    """Colour image → opaque black-and-white image (RGBA) at the target size."""
    if not 0 <= black < white <= 255:
        raise ValueError(f"tone values: 0 ≤ black < white ≤ 255, not {black}/{white}")
    if channel not in CHANNELS:
        raise ValueError(f"channel: {'/'.join(CHANNELS)}, not {channel}")
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
# Greyscale (2 bit) for ArduboyG
# --------------------------------------------------------------------------
#
# The display stays black and white; ArduboyG shows three image planes in such
# quick succession that a pixel looks black, dark grey, light grey or white
# depending on the number of its lit planes (level 0–3, plane p = level > p).
#
# On the OLED, grey shades look considerably brighter than their values. What
# looks balanced in a preview is washed out on the device; the values in
# game.adv are therefore chosen on the device. Proven in practice:
#
#   - flat levels instead of dithering: dither patterns flicker visibly with three planes,
#   - a little gamma (> 1), which darkens the midtones,
#   - isolate a subject that gets lost in the grey (the ghost ship in the lava,
#     the logo against the sky): its own, brighter tone curve and a
#     black outline of 1 px.
#
# Description of an image (from a greyscale block in game.adv):
#   layers:   list of layers (dict), each applies from its edge "from"
#             (x in the original, 0 = whole image) up to the next one,
#   subjects: list of subjects (dict) with "mask" ("hue", name) or
#             ("polygon", [(x, y), …]) in the original, plus tone options,
#             "min" (darkest level) and "outline".
# Tone options: "channel" or "weights" (r, g, b), "tone" (black, white)
# or "auto", "contrast", "gamma", "max" (brightest level), "dither".

GREY_LEVELS = 4
GREY_PLANES = GREY_LEVELS - 1
# Automatic tone range: brightness percentiles within the affected area
AUTO_TONE = (3, 99.5)

# Colour masks for subjects, on the original image (values 0–255 per channel)
_HUES = {
    # Ghost ship: blue against red lava
    "blue": lambda r, g, b: b > r + 15,
    # Monkey Island logo: magenta against blue sky
    "magenta": lambda r, g, b: (r > 90) & (b > 70) & (g < r * 0.6),
}
HUES = tuple(_HUES)
# Share of the subject in a target pixel from which it counts as subject
_HUE_COVER = 0.4
_POLYGON_COVER = 0.5


def _value(small, opts, default_channel):
    """Brightness 0–255 per pixel from a downscaled RGB image."""
    if "weights" in opts:
        return np.asarray(small, dtype=float) @ np.array(opts["weights"], dtype=float)
    return _brightness(small, opts.get("channel", default_channel))


def _graded(small, opts, defaults, area):
    """Tone curve of a layer or subject: values 0–1 per pixel. area
    (bool mask) is the reference for tone auto: for layers the whole image
    (all layers equally exposed), for subjects the subject itself (it should
    stand out)."""
    channel = defaults["channel"]
    v = _value(small, opts, channel)
    contrast = opts.get("contrast", 0.0)
    if contrast:
        blurred = _value(small.filter(ImageFilter.GaussianBlur(3)), opts, channel)
        v = v + contrast * (v - blurred)
    tone = opts.get("tone", defaults["tone"])
    if tone == "auto":
        if not area.any():
            raise ValueError("tone auto: the area is empty")
        tone = tuple(np.percentile(v[area], AUTO_TONE))
        if tone[1] <= tone[0]:
            raise ValueError(f"tone auto: the area is equally bright everywhere ({tone[0]:.0f}); "
                             "give tone S W")
    black, white = tone
    if white <= black:
        raise ValueError(f"tone values: black < white, not {black}/{white}")
    return np.clip((v - black) / (white - black), 0, 1) ** opts.get("gamma", 1.0)


def _quantize(t, lo, hi, dither):
    """Values 0–1 → levels lo..hi; flat or with Atkinson dithering."""
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
    """Colour image → greyscale (np.uint8, levels 0–3) at the target size.
    tone and channel are the room's defaults for layers without their own."""
    if not layers:
        raise ValueError("greyscale needs at least one layer")
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
    """Chapter card (brightness of the brightest channel, already cropped and
    downscaled) → levels 0–3. The card is lettering: black from half the
    threshold, white at the typical brightness of the lettering itself."""
    text = bright[bright > threshold]
    white = np.percentile(text, 60) if text.size else 255
    t = np.clip((bright - threshold / 2) / max(1.0, white - threshold / 2), 0, 1) ** gamma
    return _quantize(t, 0, GREY_PLANES, dither)


def grey_planes(levels, opaque=None):
    """Levels → the three planes as black-and-white images (RGBA); opaque
    (bool mask) makes the remaining pixels transparent (characters)."""
    planes = []
    for p in range(GREY_PLANES):
        on = (levels > p).astype(np.uint8) * 255
        rgba = np.zeros(levels.shape + (4,), dtype=np.uint8)
        rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = on
        rgba[..., 3] = 255 if opaque is None else np.where(opaque, 255, 0)
        planes.append(Image.fromarray(rgba, "RGBA"))
    return planes


def grey_preview(levels):
    """Levels → greyscale image for viewing (0, 85, 170, 255)."""
    return Image.fromarray((levels * (255 // GREY_PLANES)).astype(np.uint8), "L")


def figure_levels(strip, fw):
    """Strip from _figure_grey → per frame (levels, opaque)."""
    a = np.asarray(strip)
    return [(a[:, x:x + fw, 0] // (255 // GREY_PLANES), a[:, x:x + fw, 3] > 0)
            for x in range(0, strip.width, fw)]


# SCUMM v4 facing directions (0 west, 1 east, 2 south, 3 north) → engine
# (0 right, 1 left, 2 front). There are no frames facing away, so front.
SCUMM_DIRS = {0: 1, 1: 0, 2: 2, 3: 2}


# --------------------------------------------------------------------------
# Characters from original costumes
# --------------------------------------------------------------------------
#
# At 1/2 original size, about 10×26 pixels remain of a character. Any form
# of dithering turns that into a blob. What stays legible is a bright silhouette
# in the original shape, in which only the darkest parts (trousers, hair,
# coat) are black, with a black outline against the dithered background.

# Render canvas for a costume: large enough for all characters in the game,
# foot point at the bottom centre.
_CANVAS = (240, 240)
_ORIGIN = (120, 200)


def _silhouette(img, dark, outline):
    """Scaled RGBA → white silhouette, very dark pixels black, outline."""
    a = np.asarray(img, dtype=float)
    alpha = a[..., 3] > 110
    lum = a[..., 0] * 0.35 + a[..., 1] * 0.5 + a[..., 2] * 0.15
    h, w = alpha.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[alpha] = (255, 255, 255, 255)
    out[alpha & (lum < dark)] = (0, 0, 0, 255)
    opaque = alpha.copy()
    for step in range(outline):
        # Outline grows first around the white pixels, then around everything opaque.
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
    """Brightness of the character pixels (as in _silhouette)."""
    return a[..., 0] * 0.35 + a[..., 1] * 0.5 + a[..., 2] * 0.15


def _figure_grey(img, dark, outline, tone):
    """Scaled RGBA → character in greyscale (RGBA, grey 0/85/170/255):
    the same shape and outline as _silhouette, but the costume's brightness
    values instead of a white area. Very dark pixels stay black, the rest
    are spread over levels 1–3 via tone (black, white) – a character thus
    never gets darker than dark grey and, with the black outline, stands
    out against any background."""
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


# Tone range of a character in greyscale: brightness percentiles over all
# frames of a costume (excluding the black parts) – shared, so that a
# character does not change brightness while walking.
FIGURE_TONE = (10, 95)


def costume_frames(costume, palette, sequences, scale, dark=45, outline=1, grey=False):
    """Renders costume frames and brings them to a common frame size.

    sequences: list of (actions, direction, steps) – the frame shows the
    state after applying the actions and advancing steps times.
    Returns: (strip as RGBA, frame width, frame height). The foot point lies
    at the bottom centre of the frame, as the engine draws actors. With
    grey=True, additionally the same strip in greyscale as a fourth value
    (_figure_grey: grey 0/85/170/255, alpha 0/255).
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
        raise ValueError(f"costume {costume.number}: no visible pixels")
    ox, oy = _ORIGIN
    half = max(max(ox - b[0], b[2] - ox) for b in boxes)
    top = min(b[1] for b in boxes)
    crop = (ox - half, top, ox + half, oy + 1)   # the frame ends at the foot at the bottom
    w = max(1, scaled(crop[2] - crop[0], scale))
    h = max(1, scaled(crop[3] - crop[1], scale))
    fw, fh = w + 2 * outline, h + outline        # outline left, right, top
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
    """Number of steps until an animation repeats."""
    state = costume.new_state()
    for action in actions:
        costume.apply(state, action, direction)
    first = list(state["pos"])
    for n in range(1, limit + 1):
        costume.advance(state)
        if state["pos"] == first:
            return n
    return limit
