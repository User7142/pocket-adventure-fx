// Host stand-in for the Arduboy2 API: only what the engine uses. Drawing
// goes to host::screen; keys are set by the test bench (host::buttons).
#pragma once

constexpr int16_t WIDTH = 128;
constexpr int16_t HEIGHT = 64;
// Macros as in the original: ArduboyG replaces them with its greyscale.
#define BLACK 0
#define WHITE 1
constexpr uint8_t LEFT_BUTTON = 0x20, RIGHT_BUTTON = 0x40, UP_BUTTON = 0x80, DOWN_BUTTON = 0x10,
                  A_BUTTON = 0x08, B_BUTTON = 0x04;
constexpr uint16_t EEPROM_STORAGE_SPACE_START = 16;

class __FlashStringHelper;
#define F(s) (reinterpret_cast<const __FlashStringHelper*>(s))
#define PROGMEM
#define pgm_read_byte(p) (*reinterpret_cast<const uint8_t*>(p))

namespace host {
  extern uint8_t buttons;  // keys pressed in this frame
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
  extern uint8_t screen[WIDTH * HEIGHT / 8];  // frame buffer in Arduboy layout (pages of 8 rows)
  extern const uint8_t font5x7[];             // from the Arduboy2 library (Makefile)
  extern bool inverted;                       // display inverted (Arduboy2::invert)
  inline void pixel(int16_t x, int16_t y, uint8_t color) {
    if (x < 0 || x >= WIDTH || y < 0 || y >= HEIGHT) return;
    uint8_t& b = screen[(y / 8) * WIDTH + x];
    if (color) b |= 1 << (y & 7);
    else b &= ~(1 << (y & 7));
  }
}

// Like Print of the Arduino core: texts end up in the virtual write(buffer, n),
// which, unless overridden, calls write(c) character by character.
class Print {
 public:
  virtual ~Print() = default;
  virtual size_t write(uint8_t c) = 0;
  virtual size_t write(const uint8_t* s, size_t n) {
    size_t done = 0;
    while (n--) done += write(*s++);
    return done;
  }
  size_t write(const char* s) { return write(reinterpret_cast<const uint8_t*>(s), std::strlen(s)); }
  void print(const char* s) { write(s); }
  void print(char c) { write(uint8_t(c)); }
  void print(const __FlashStringHelper* s) { write(reinterpret_cast<const char*>(s)); }
};

class Arduboy2 : public Print {
 public:
  void begin() {}
  void pollButtons() { previous = current; current = host::buttons; }
  bool pressed(uint8_t b) { return (current & b) == b; }
  bool justPressed(uint8_t b) { return (current & b) && !(previous & b); }

  // Text with text state as in Arduboy2 (cursor, colours, wrapping).
  void setCursor(int16_t x, int16_t y) { cursor_x = x; cursor_y = y; }
  void setTextColor(uint8_t c) { textColor = c; }
  void setTextBackground(uint8_t c) { textBackground = c; }
  using Print::write;
  size_t write(uint8_t c) override {
    if (c == '\r' && !textRaw) return 1;
    if ((c == '\n' && !textRaw) || (textWrap && cursor_x > WIDTH - characterWidth)) {
      cursor_x = 0;
      cursor_y += fullCharacterHeight;
    }
    if (c != '\n' || textRaw) {
      drawChar(cursor_x, cursor_y, c, textColor, textBackground);
      cursor_x += fullCharacterWidth;
    }
    return 1;
  }
  // Like Arduboy2::drawChar (text size 1): pixel by pixel. Stays here as the
  // reference against which the test bench measures the engine's fast output.
  static void drawChar(int16_t x, int16_t y, uint8_t c, uint8_t color, uint8_t bg) {
    bool drawBackground = bg != color;
    for (uint8_t i = 0; i < fullCharacterWidth; ++i) {
      uint8_t column = i < characterWidth ? font5x7[c * characterWidth + i] : 0;
      for (uint8_t j = 0; j < fullCharacterHeight; ++j, column >>= 1) {
        if ((column & 1) || drawBackground) host::pixel(x + i, y + j, (column & 1) ? color : bg);
      }
    }
  }
  static uint8_t* getBuffer() { return host::screen; }

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
  static constexpr const uint8_t* font5x7 = host::font5x7;

 protected:
  static constexpr uint8_t characterWidth = 5, fullCharacterWidth = 6;
  static constexpr uint8_t characterHeight = 8, fullCharacterHeight = 8;
  static inline int16_t cursor_x = 0, cursor_y = 0;
  static inline uint8_t textColor = WHITE, textBackground = BLACK;
  static inline bool textWrap = false, textRaw = false;

 private:
  uint8_t current = 0, previous = 0;
};
