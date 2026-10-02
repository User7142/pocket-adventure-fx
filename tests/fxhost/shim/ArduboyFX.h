// Host-Nachbildung des ArduboyFX-API: der FX-Flash ist game.bin im Speicher.
#pragma once

using uint24_t = __uint24;

constexpr uint8_t dbmNormal = 0, dbmMasked = 1 << 4, dbmFlip = 1 << 5;  // Bits wie ArduboyFX

namespace host {
  extern std::vector<uint8_t> flash;
  // Wird bei jedem Zeichenaufruf gemeldet (der Prüfstand findet so z. B. den Cursor).
  void drawn(int16_t x, int16_t y, uint32_t image, uint8_t frame);
}

namespace FX {
  inline uint32_t cursor;
  inline void begin(uint16_t) {}
  // Display und Flash teilen sich auf dem Gerät den SPI-Bus; hier ohne Wirkung.
  inline void enableOLED() {}
  inline void disableOLED() {}
  inline void readDataBytes(uint24_t address, uint8_t* buffer, size_t length) {
    for (size_t i = 0; i < length; ++i) {
      uint32_t a = uint32_t(address) + i;
      buffer[i] = a < host::flash.size() ? host::flash[a] : 0xFF;
    }
  }
  template <class T>
  void readDataObject(uint24_t address, T& object) {
    readDataBytes(address, reinterpret_cast<uint8_t*>(&object), sizeof(T));
  }
  inline void seekData(uint24_t address) { cursor = address; }
  inline uint8_t readPendingUInt8() {
    uint8_t v;
    readDataBytes(cursor, &v, 1);
    cursor += 1;
    return v;
  }
  inline uint16_t readPendingUInt16() {  // big-endian wie das Original
    uint16_t hi = readPendingUInt8();
    return (hi << 8) | readPendingUInt8();
  }
  inline uint16_t readPendingLastUInt16() { return readPendingUInt16(); }
  inline uint8_t readPendingLastUInt8() { return readPendingUInt8(); }
  inline void readEnd() {}

  // Wie FX::drawBitmap: Bild in Seiten à 8 Zeilen, Spalte für Spalte; mit
  // Maske je Bildbyte ein Maskenbyte dahinter; dbmFlip spiegelt waagerecht.
  inline void drawBitmap(int16_t x, int16_t y, uint24_t image, uint8_t frame, uint8_t mode) {
    host::drawn(x, y, image, frame);
    seekData(image);
    int16_t w = readPendingUInt16();
    int16_t h = readPendingUInt16();
    uint16_t pages = (h + 7) / 8;
    bool masked = mode & dbmMasked;
    uint32_t base = uint32_t(image) + 4;
    for (int16_t cy = 0; cy < h; ++cy) {
      for (int16_t cx = 0; cx < w; ++cx) {
        uint32_t i = (uint32_t(frame) * pages + cy / 8) * w + cx;
        uint8_t bit = 1 << (cy & 7);
        uint8_t px, mask = 0xFF;
        if (masked) {
          readDataBytes(base + 2 * i, &px, 1);
          readDataBytes(base + 2 * i + 1, &mask, 1);
        } else {
          readDataBytes(base + i, &px, 1);
        }
        if (!(mask & bit)) continue;
        host::pixel((mode & dbmFlip) ? x + w - 1 - cx : x + cx, y + cy, px & bit);
      }
    }
  }
}
