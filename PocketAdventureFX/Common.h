#pragma once

// Graustufen über ArduboyG (libraries/ArduboyG): drei Bildebenen, so schnell
// nacheinander gezeigt, dass ein Pixel je nach Zahl seiner hellen Ebenen
// schwarz, dunkelgrau, hellgrau oder weiß wirkt. Die Konfiguration muss in
// jeder Übersetzungseinheit gleich sein, darum steht sie hier.
//
// Timer4 für den Ebenentakt: Timer1 und Timer3 belegt der Musikplayer
// (Sound.h). Park-Zeile: höchste Bildrate (weniger Flimmern), dafür zeigt das
// Display die unterste Pixelzeile nicht.
#define ABG_TIMER4
#define ABG_SYNC_PARK_ROW
#include <ArduboyG.h>
#include <ArduboyFX.h>
#include "gamedata.h"

// Bei drei Ebenen je Bild (156 Ebenen pro Sekunde) bleiben für Logik und
// Zeichnen einer Ebene gut 5 ms; überzieht eine, steht sie zu lange und das
// Bild flackert. Zwei Arduboy2-Funktionen setzen jedes Pixel einzeln und
// sprengen das allein: drawChar (48 Pixel je Zeichen) und fillRect (das
// Rechteck Pixel für Pixel – der Kasten einer Sprechblase kostete 8 ms).
// Beide sind hier ersetzt (Display.cpp) und arbeiten seitenweise auf dem
// Bildpuffer: Eine Seite sind 8 Zeilen, je Spalte ein Byte, und eine
// Fontspalte ist genau so ein Byte. Ergebnis wie das Original (Text in
// Größe 1), Farben je Ebene wie ArduboyG (color()).
class Arduboy : public ArduboyG_Config<ABG_Mode::L4_Triplane> {
 public:
  size_t write(uint8_t c) override;
  size_t write(const uint8_t* text, size_t n) override;  // print() landet hier
  using Print::write;
  static void fillRect(int16_t x, int16_t y, uint8_t w, uint8_t h, uint8_t color = WHITE);

 private:
  void drawRun(const uint8_t* text, size_t n);  // Zeichen einer Zeile ab dem Cursor
};
extern Arduboy arduboy;

// Graustufen an (Vorgabe) oder Schwarz-Weiß; im Menü umschaltbar.
extern bool greyscale;
namespace Settings {
  void setGreyscale(bool on);  // umschalten und im EEPROM merken
}

constexpr uint8_t NONE8 = 0xFF;
constexpr uint24_t NONE24 = 0xFFFFFF;

// Platzhalter im Text, danach Platz + 1: String-Variable bzw. Zahl (vars).
constexpr char STRING_VAR = 0x01;
constexpr char INT_VAR = 0x03;

// Die Spielfigur ist per Konvention der erste Actor der Spielbeschreibung.
constexpr uint8_t PLAYER = 0;

enum Dir : uint8_t { DIR_RIGHT = 0, DIR_LEFT = 1, DIR_FRONT = 2 };

// Little-endian-Operanden aus einem gelesenen Befehlspuffer.
inline uint16_t le16(const uint8_t* p) { return p[0] | (uint16_t(p[1]) << 8); }
inline uint24_t le24(const uint8_t* p) { return p[0] | (uint24_t(p[1]) << 8) | (uint24_t(p[2]) << 16); }

// Füllt das ganze Display aus einem deckenden Bild (Raum, Titel, Karte), das
// es in 1 Bit und in Graustufen geben kann, ab Bildpunkt (sx, sy). Ist sy ein
// Vielfaches von 8, liegen die Seiten des Bilds bündig auf denen des
// Displays: Dann wird jede Seite direkt aus dem FX-Flash in den Puffer
// gelesen (etwa 1 µs je Byte statt 2,7 µs mit FX::drawBitmap, das bei jedem
// Byte verschiebt und mischt). Sonst – die Karte, senkrecht gescrollt –
// zeichnet FX::drawBitmap. Das Bild muss das Display ganz bedecken.
void drawScreen(int16_t sx, int16_t sy, uint24_t mono, uint24_t grey);

// Zeichnet ein Bild, das es in 1 Bit und in Graustufen geben kann (advc.py:
// Frame f des 1-Bit-Bilds liegt in der Graustufen-Fassung als Frames 3f..3f+2,
// einer je Ebene). Ohne Graustufen-Fassung oder im Schwarz-Weiß-Modus gilt
// das 1-Bit-Bild auf allen Ebenen: Weiß bleibt weiß, Schwarz schwarz.
inline void drawImage(int16_t x, int16_t y, uint24_t mono, uint24_t grey, uint8_t frame, uint8_t mode) {
  if (greyscale && grey != NONE24)
    FX::drawBitmap(x, y, grey, frame * 3 + arduboy.currentPlane(), mode);
  else
    FX::drawBitmap(x, y, mono, frame, mode);
}
