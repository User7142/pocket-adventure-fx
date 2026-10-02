// Drawing to the screen buffer, fast enough for three planes per frame
// (Common.h: Arduboy). The buffer is organised in pages of 8 rows, one
// byte per column (bit 0 at the top); a font column is exactly such a byte.
//
// Measured in the Ardens emulator (16 MHz), per plane: Arduboy2::drawChar 200 µs
// per character, the column output here about 5 µs; Arduboy2::fillRect for the
// box of a speech bubble 8 ms, page by page 0.35 ms; FX::drawBitmap for
// a background 2.8 ms, read page by page 1.7 ms.

// This file draws per plane, i.e. 156 times per second: compile for speed
// instead of size (-Os, the Arduino core's default). That costs
// a few hundred bytes of flash.
#pragma GCC optimize("O2")

#include "Common.h"

namespace {
  // 1 << n as a table: from the constant 1 << n the compiler makes a
  // shift loop (one cycle per bit position and step), from a table value
  // a real multiplication (2 cycles) that yields both pages at once.
  const uint8_t SHIFT_FACTOR[8] PROGMEM = {1, 2, 4, 8, 16, 32, 64, 128};

  // What an opaque character needs in this plane and row offset; computed once
  // per line (Arduboy::drawRun).
  struct Ink {
    uint8_t flip, keep, fill;     // column byte = ((font column ^ flip) & keep) | fill
    uint8_t space;                // the empty sixth column
    uint8_t factor;               // 1 << row offset
    uint8_t keepTop, keepBottom;  // rows of the two pages outside the character
  };

  // A whole, opaque character. Separate small functions so that all
  // values of the column loop fit into registers: in drawRun itself too many
  // are in use at once, and the compiler spilled them to the stack per column
  // (three times as slow).
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

// Like Arduboy2::write per character: skip '\r'; '\n' and – with
// textWrap – the right edge start a new line. The characters in between
// are drawn by drawRun in one go.
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
    // Characters up to the next control character or (with textWrap) up to the edge
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
  const int8_t page = y >> 3;  // also for y < 0: arithmetic, rounded down
  const uint8_t shift = y & 7;
  const bool upper = page >= 0 && page < HEIGHT / 8;
  const bool lower = shift && page + 1 >= 0 && page + 1 < HEIGHT / 8;
  if (!upper && !lower) return;
  uint8_t* const top = upper ? getBuffer() + page * WIDTH : nullptr;
  uint8_t* const bottom = lower ? getBuffer() + (page + 1) * WIDTH : nullptr;

  // Colours in this plane (0/1). Equal text and background colour means,
  // as with drawChar: leave the background transparent. Otherwise a
  // column byte is v = ((font column ^ flip) & keep) | fill – white on black
  // is the font column itself, black on white its inverse, solid if
  // both colours are equal in this plane (grey).
  const bool fg = color(textColor), bg = color(textBackground);
  const bool transparent = textColor == textBackground;
  const uint8_t flip = fg ? 0 : 0xFF;
  const uint8_t keep = fg != bg ? 0xFF : 0;
  const uint8_t fill = fg == bg && fg ? 0xFF : 0;
  // Offset: bottom of the upper page, top of the lower one (16-bit product);
  // the remaining rows of both bytes are kept (keepTop, keepBottom).
  const uint8_t factor = pgm_read_byte(SHIFT_FACTOR + shift);
  const uint8_t keepTop = factor - 1;
  const uint8_t keepBottom = ~keepTop;

  const uint8_t space = (flip & keep) | fill;  // the empty sixth column
  const Ink ink = {flip, keep, fill, space, factor, keepTop, keepBottom};
  for (; n--; x += fullCharacterWidth) {
    const uint8_t* glyph = font5x7 + *text++ * characterWidth;
    if (x >= WIDTH) return;
    if (x <= -int16_t(fullCharacterWidth)) continue;

    // The common case: opaque and entirely on the display.
    if (!transparent && x >= 0 && x <= WIDTH - fullCharacterWidth) {
      if (!shift) {
        // Aligned: the cell is exactly the byte – just write it.
        glyphAligned(top + x, glyph, ink);
        continue;
      }
      if (top && bottom) {
        // Offset, in the middle of the image: both pages
        glyphShifted(top + x, bottom + x, glyph, ink);
        continue;
      }
    }

    // Clipped at the edge or transparent: column by column
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
    // Rows of this page that lie inside the rectangle
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
