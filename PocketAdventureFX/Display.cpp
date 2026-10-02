// Zeichnen auf den Bildpuffer, schnell genug für drei Ebenen je Bild
// (Common.h: Arduboy). Der Puffer ist in Seiten zu 8 Zeilen geordnet, je
// Spalte ein Byte (Bit 0 oben); eine Fontspalte ist genau so ein Byte.
//
// Gemessen im Emulator Ardens (16 MHz), je Ebene: Arduboy2::drawChar 200 µs
// je Zeichen, die Spaltenausgabe hier etwa 5 µs; Arduboy2::fillRect für den
// Kasten einer Sprechblase 8 ms, seitenweise 0,35 ms; FX::drawBitmap für
// einen Hintergrund 2,8 ms, seitenweise gelesen 1,7 ms.

// Diese Datei zeichnet je Ebene, also 156-mal pro Sekunde: auf Tempo
// übersetzen statt auf Größe (-Os, Vorgabe des Arduino-Kerns). Das kostet
// einige hundert Byte Flash.
#pragma GCC optimize("O2")

#include "Common.h"

namespace {
  // 1 << n als Tabelle: Aus der Konstante 1 << n macht der Compiler eine
  // Schiebeschleife (ein Takt je Stelle und Schritt), aus einem Tabellenwert
  // eine echte Multiplikation (2 Takte), die beide Seiten auf einmal liefert.
  const uint8_t SHIFT_FACTOR[8] PROGMEM = {1, 2, 4, 8, 16, 32, 64, 128};

  // Was ein deckendes Zeichen in dieser Ebene und Zeilenlage braucht; einmal
  // je Zeile berechnet (Arduboy::drawRun).
  struct Ink {
    uint8_t flip, keep, fill;     // Spaltenbyte = ((Fontspalte ^ flip) & keep) | fill
    uint8_t space;                // die leere sechste Spalte
    uint8_t factor;               // 1 << Zeilenversatz
    uint8_t keepTop, keepBottom;  // Zeilen der beiden Seiten außerhalb des Zeichens
  };

  // Ein ganzes, deckendes Zeichen. Eigene kleine Funktionen, damit alle
  // Werte der Spaltenschleife in Register passen: In drawRun selbst sind zu
  // viele zugleich in Gebrauch, der Compiler lagerte sie je Spalte auf den
  // Stapel aus (dreimal so langsam).
  __attribute__((noinline)) void glyphAligned(uint8_t* d, const uint8_t* glyph, const Ink& ink) {
    const uint8_t flip = ink.flip, keep = ink.keep, fill = ink.fill;
    for (uint8_t i = 0; i < 5; ++i) *d++ = ((pgm_read_byte(glyph++) ^ flip) & keep) | fill;
    *d = ink.space;
  }

  __attribute__((noinline)) void glyphShifted(uint8_t* t, uint8_t* b, const uint8_t* glyph, const Ink& ink) {
    const uint8_t flip = ink.flip, keep = ink.keep, fill = ink.fill, factor = ink.factor;
    const uint8_t keepTop = ink.keepTop, keepBottom = ink.keepBottom;
    for (uint8_t i = 0; i < 6; ++i) {
      uint8_t column = i < 5 ? ((pgm_read_byte(glyph++) ^ flip) & keep) | fill : ink.space;
      uint16_t v = column * factor;
      *t = (*t & keepTop) | uint8_t(v);
      ++t;
      *b = (*b & keepBottom) | uint8_t(v >> 8);
      ++b;
    }
  }
}

void drawScreen(int16_t sx, int16_t sy, uint24_t mono, uint24_t grey) {
  bool useGrey = greyscale && grey != NONE24;
  uint24_t image = useGrey ? grey : mono;
  uint8_t frame = useGrey ? arduboy.currentPlane() : 0;
  if (sy & 7) {
    FX::drawBitmap(-sx, -sy, image, frame, dbmNormal);
    return;
  }
  FX::seekData(image);
  uint16_t w = FX::readPendingUInt16();
  uint16_t h = FX::readPendingLastUInt16();
  uint24_t pixels = image + 4 + uint24_t(frame) * (h / 8) * w + uint24_t(sy / 8) * w + sx;
  uint8_t* buffer = arduboy.getBuffer();
  for (uint8_t page = 0; page < HEIGHT / 8; ++page, pixels += w)
    FX::readDataBytes(pixels, buffer + page * WIDTH, WIDTH);
}

size_t Arduboy::write(uint8_t c) {
  return write(&c, 1);
}

// Wie Arduboy2::write je Zeichen: '\r' überspringen, '\n' und – mit
// textWrap – der rechte Rand beginnen eine neue Zeile. Die Zeichen dazwischen
// zeichnet drawRun am Stück.
size_t Arduboy::write(const uint8_t* text, size_t n) {
  size_t i = 0;
  while (i < n) {
    uint8_t c = text[i];
    if (!textRaw && (c == '\r' || c == '\n')) {
      if (c == '\n') {
        cursor_x = 0;
        cursor_y += fullCharacterHeight;
      }
      ++i;
      continue;
    }
    if (textWrap && cursor_x > WIDTH - characterWidth) {
      cursor_x = 0;
      cursor_y += fullCharacterHeight;
    }
    // Zeichen bis zum nächsten Steuerzeichen bzw. (mit textWrap) bis zum Rand
    size_t end = i;
    int16_t x = cursor_x;
    while (end < n && (textRaw || (text[end] != '\r' && text[end] != '\n')) &&
           (!textWrap || end == i || x <= WIDTH - characterWidth)) {
      ++end;
      x += fullCharacterWidth;
    }
    drawRun(text + i, end - i);
    i = end;
  }
  return n;
}

void Arduboy::drawRun(const uint8_t* text, size_t n) {
  int16_t x = cursor_x;
  cursor_x += n * fullCharacterWidth;
  const int16_t y = cursor_y;
  const int8_t page = y >> 3;  // auch für y < 0: arithmetisch, abgerundet
  const uint8_t shift = y & 7;
  const bool upper = page >= 0 && page < HEIGHT / 8;
  const bool lower = shift && page + 1 >= 0 && page + 1 < HEIGHT / 8;
  if (!upper && !lower) return;
  uint8_t* const top = upper ? getBuffer() + page * WIDTH : nullptr;
  uint8_t* const bottom = lower ? getBuffer() + (page + 1) * WIDTH : nullptr;

  // Farben in dieser Ebene (0/1). Gleiche Text- und Hintergrundfarbe heißt
  // wie bei drawChar: Hintergrund durchsichtig lassen. Sonst ist ein
  // Spaltenbyte v = ((Fontspalte ^ flip) & keep) | fill – Weiß auf Schwarz
  // die Fontspalte selbst, Schwarz auf Weiß ihr Gegenteil, einfarbig, wenn
  // beide Farben in dieser Ebene gleich sind (Grau).
  const bool fg = color(textColor), bg = color(textBackground);
  const bool transparent = textColor == textBackground;
  const uint8_t flip = fg ? 0 : 0xFF;
  const uint8_t keep = fg != bg ? 0xFF : 0;
  const uint8_t fill = fg == bg && fg ? 0xFF : 0;
  // Versetzt: unten in der oberen Seite, oben in der unteren (16-Bit-Produkt);
  // die übrigen Zeilen beider Bytes bleiben (keepTop, keepBottom).
  const uint8_t factor = pgm_read_byte(SHIFT_FACTOR + shift);
  const uint8_t keepTop = factor - 1;
  const uint8_t keepBottom = ~keepTop;

  const uint8_t space = (flip & keep) | fill;  // die leere sechste Spalte
  const Ink ink = {flip, keep, fill, space, factor, keepTop, keepBottom};
  for (; n--; x += fullCharacterWidth) {
    const uint8_t* glyph = font5x7 + *text++ * characterWidth;
    if (x >= WIDTH) return;
    if (x <= -int16_t(fullCharacterWidth)) continue;

    // Der Normalfall: deckend und ganz auf dem Display.
    if (!transparent && x >= 0 && x <= WIDTH - fullCharacterWidth) {
      if (!shift) {
        // Bündig: Die Zelle ist genau das Byte – nur schreiben.
        glyphAligned(top + x, glyph, ink);
        continue;
      }
      if (top && bottom) {
        // Versetzt, mitten im Bild: beide Seiten
        glyphShifted(top + x, bottom + x, glyph, ink);
        continue;
      }
    }

    // Am Rand angeschnitten oder durchsichtig: Spalte für Spalte
    const uint8_t first = x < 0 ? -x : 0;
    const uint8_t last = x + fullCharacterWidth > WIDTH ? WIDTH - x : fullCharacterWidth;
    for (uint8_t i = first; i < last; ++i) {
      uint8_t font = i < characterWidth ? pgm_read_byte(glyph + i) : 0;
      if (transparent) {
        uint16_t v = font * factor;
        if (top) top[x + i] = fg ? top[x + i] | uint8_t(v) : top[x + i] & ~uint8_t(v);
        if (bottom) bottom[x + i] = fg ? bottom[x + i] | uint8_t(v >> 8) : bottom[x + i] & ~uint8_t(v >> 8);
      } else {
        uint16_t v = uint8_t(((font ^ flip) & keep) | fill) * factor;
        if (top) top[x + i] = (top[x + i] & keepTop) | uint8_t(v);
        if (bottom) bottom[x + i] = (bottom[x + i] & keepBottom) | uint8_t(v >> 8);
      }
    }
  }
}

void Arduboy::fillRect(int16_t x, int16_t y, uint8_t w, uint8_t h, uint8_t c) {
  int16_t x1 = x + w, y1 = y + h;
  if (x < 0) x = 0;
  if (y < 0) y = 0;
  if (x1 > WIDTH) x1 = WIDTH;
  if (y1 > HEIGHT) y1 = HEIGHT;
  if (x >= x1 || y >= y1) return;
  const bool on = color(c);
  uint8_t* buffer = getBuffer();
  for (int16_t page = y >> 3; page <= (y1 - 1) >> 3; ++page) {
    // Zeilen dieser Seite, die im Rechteck liegen
    uint8_t top = page * 8 < y ? y - page * 8 : 0;
    uint8_t bottom = page * 8 + 8 > y1 ? y1 - page * 8 : 8;
    uint8_t mask = uint8_t(0xFF << top) & uint8_t(0xFF >> (8 - bottom));
    uint8_t* b = buffer + page * WIDTH + x;
    uint8_t* end = buffer + page * WIDTH + x1;
    if (on) {
      for (; b < end; ++b) *b |= mask;
    } else {
      mask = ~mask;
      for (; b < end; ++b) *b &= mask;
    }
  }
}
