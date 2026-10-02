// Host-Nachbildung des Arduboy2-API: nur, was die Engine benutzt. Zeichnen
// ist wirkungslos; Tasten setzt der Prüfstand (host::buttons).
#pragma once

constexpr int16_t WIDTH = 128;
constexpr int16_t HEIGHT = 64;
constexpr uint8_t BLACK = 0, WHITE = 1;
constexpr uint8_t LEFT_BUTTON = 0x20, RIGHT_BUTTON = 0x40, UP_BUTTON = 0x80, DOWN_BUTTON = 0x10,
                  A_BUTTON = 0x08, B_BUTTON = 0x04;
constexpr uint16_t EEPROM_STORAGE_SPACE_START = 16;

class __FlashStringHelper;
#define F(s) (reinterpret_cast<const __FlashStringHelper*>(s))

namespace host {
  extern uint8_t buttons;  // gedrückte Tasten in diesem Frame
}

long random(long howbig);

class Arduboy2Audio {
 public:
  void toggle() { on = !on; }
  void saveOnOff() {}
  bool enabled() { return on; }
  static void off() {}
  bool on = true;
};

namespace host {
  extern uint8_t screen[WIDTH * HEIGHT / 8];  // Bildpuffer im Arduboy-Layout (Seiten à 8 Zeilen)
  extern const uint8_t font5x7[];             // aus der Arduboy2-Bibliothek (Makefile)
  extern bool inverted;                       // Display invertiert (Arduboy2::invert)
  inline void pixel(int16_t x, int16_t y, uint8_t color) {
    if (x < 0 || x >= WIDTH || y < 0 || y >= HEIGHT) return;
    uint8_t& b = screen[(y / 8) * WIDTH + x];
    if (color) b |= 1 << (y & 7);
    else b &= ~(1 << (y & 7));
  }
}

class Arduboy2 {
 public:
  void begin() {}
  void setFrameRate(uint8_t) {}
  bool nextFrame() { ++frameCount; return true; }
  void pollButtons() { previous = current; current = host::buttons; }
  bool pressed(uint8_t b) { return (current & b) == b; }
  bool justPressed(uint8_t b) { return (current & b) && !(previous & b); }

  // Text wie Arduboy2::write/drawChar: 5×8-Zeichen, 6 px Vorschub
  void setCursor(int16_t x, int16_t y) { cx = x; cy = y; }
  void setTextColor(uint8_t c) { color = c; }
  void setTextBackground(uint8_t c) { background = c; }
  size_t write(uint8_t c) {
    if (c == '\n') {
      cx = 0;
      cy += 8;
    } else if (c != '\r') {
      const uint8_t* glyph = &host::font5x7[c * 5];
      for (uint8_t i = 0; i < 6; ++i) {
        uint8_t column = i < 5 ? glyph[i] : 0;
        for (uint8_t j = 0; j < 8; ++j, column >>= 1) {
          if ((column & 1) || background != color) host::pixel(cx + i, cy + j, (column & 1) ? color : background);
        }
      }
      cx += 6;
    }
    return 1;
  }
  void print(const char* s) { while (*s) write(*s++); }
  void print(char c) { write(c); }
  void print(const __FlashStringHelper* s) { print(reinterpret_cast<const char*>(s)); }

  void fillRect(int16_t x, int16_t y, uint8_t w, uint8_t h, uint8_t c = WHITE) {
    for (int16_t i = 0; i < w; ++i)
      for (int16_t j = 0; j < h; ++j) host::pixel(x + i, y + j, c);
  }
  void drawFastHLine(int16_t x, int16_t y, uint8_t w, uint8_t c = WHITE) { fillRect(x, y, w, 1, c); }
  void drawRect(int16_t x, int16_t y, uint8_t w, uint8_t h, uint8_t c = WHITE) {
    fillRect(x, y, w, 1, c);
    fillRect(x, y + h - 1, w, 1, c);
    fillRect(x, y, 1, h, c);
    fillRect(x + w - 1, y, 1, h, c);
  }
  static void setRGBled(uint8_t, uint8_t, uint8_t) {}
  void invert(bool on) { host::inverted = on; }
  uint16_t frameCount = 0;
  Arduboy2Audio audio;

 private:
  uint8_t current = 0, previous = 0;
  int16_t cx = 0, cy = 0;
  uint8_t color = WHITE, background = BLACK;
};
