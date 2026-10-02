#pragma once

// Greyscale via ArduboyG (libraries/ArduboyG): three image planes, shown in
// such quick succession that a pixel looks black, dark grey, light grey or
// white depending on how many of its planes are lit. The configuration must
// be identical in every translation unit, so it lives here.
//
// Timer4 for the plane cycle: Timer1 and Timer3 are taken by the music player
// (Sound.h). Park row: highest frame rate (less flicker), at the cost of the
// display not showing the bottom pixel row.
#define ABG_TIMER4
#define ABG_SYNC_PARK_ROW
#include <ArduboyG.h>
#include <ArduboyFX.h>
#include "gamedata.h"

// With three planes per frame (156 planes per second), logic and drawing of
// one plane get a good 5 ms; if one overruns, it stays up too long and the
// image flickers. Two Arduboy2 functions set every pixel individually and
// blow that budget on their own: drawChar (48 pixels per character) and fillRect (the
// rectangle pixel by pixel – the box of a speech bubble cost 8 ms).
// Both are replaced here (Display.cpp) and work page by page on the
// screen buffer: a page is 8 rows, one byte per column, and a
// font column is exactly such a byte. Result as in the original (text at
// size 1), colours per plane as in ArduboyG (color()).
class Arduboy : public ArduboyG_Config<ABG_Mode::L4_Triplane> {
 public:
  size_t write(uint8_t c) override;
  size_t write(const uint8_t* text, size_t n) override;  // print() ends up here
  using Print::write;
  static void fillRect(int16_t x, int16_t y, uint8_t w, uint8_t h, uint8_t color = WHITE);

 private:
  void drawRun(const uint8_t* text, size_t n);  // characters of one line, starting at the cursor
};
extern Arduboy arduboy;

// Greyscale on (default) or black and white; switchable in the menu.
extern bool greyscale;
namespace Settings {
  void setGreyscale(bool on);  // toggle and remember in EEPROM
}

constexpr uint8_t NONE8 = 0xFF;
constexpr uint24_t NONE24 = 0xFFFFFF;

// Placeholders in text, followed by slot + 1: string variable or number (vars).
constexpr char STRING_VAR = 0x01;
constexpr char INT_VAR = 0x03;

// By convention the player character is the first actor of the game description.
constexpr uint8_t PLAYER = 0;

enum Dir : uint8_t { DIR_RIGHT = 0, DIR_LEFT = 1, DIR_FRONT = 2 };

// Little-endian operands from a command buffer that has been read.
inline uint16_t le16(const uint8_t* p) { return p[0] | (uint16_t(p[1]) << 8); }
inline uint24_t le24(const uint8_t* p) { return p[0] | (uint24_t(p[1]) << 8) | (uint24_t(p[2]) << 16); }

// Fills the whole display from an opaque image (room, title, map) that may
// exist in 1 bit and in greyscale, starting at pixel (sx, sy). If sy is a
// multiple of 8, the image's pages are aligned with those of the
// display: each page is then read straight from the FX flash into the buffer
// (about 1 µs per byte instead of 2.7 µs with FX::drawBitmap, which shifts and
// blends every byte). Otherwise – the map, scrolled vertically –
// FX::drawBitmap draws it. The image must cover the whole display.
void drawScreen(int16_t sx, int16_t sy, uint24_t mono, uint24_t grey);

// Draws an image that may exist in 1 bit and in greyscale (advc.py:
// frame f of the 1-bit image is stored in the greyscale version as frames 3f..3f+2,
// one per plane). Without a greyscale version or in black-and-white mode the
// 1-bit image applies to all planes: white stays white, black stays black.
inline void drawImage(int16_t x, int16_t y, uint24_t mono, uint24_t grey, uint8_t frame, uint8_t mode) {
  if (greyscale && grey != NONE24)
    FX::drawBitmap(x, y, grey, frame * 3 + arduboy.currentPlane(), mode);
  else
    FX::drawBitmap(x, y, mono, frame, mode);
}
