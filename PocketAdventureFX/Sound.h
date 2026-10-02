#pragma once

#include "Common.h"

// Einstimmiger PC-Speaker-Player für Tracks im FX-Flash.
//
// Die Noten des Titelthemas dauern meist nur 10–18 ms und damit
// kürzer als ein Frame (16,7 ms). Das Timing läuft deshalb nicht über die
// Frame-Schleife, sondern über einen 1-kHz-Interrupt auf Timer1; Timer3
// erzeugt im CTC-Modus die Tonhöhe direkt auf dem Lautsprecher-Pin.
//
// Der Interrupt liest nur aus einem RAM-Ringpuffer. Nachgefüllt wird er
// aus dem FX-Flash in update(), also im Hauptprogramm – so kommen sich der
// Interrupt und die SPI-Zugriffe auf Flash und Display nie in die Quere.
//
// Belegt Timer1: Arduboy2::setRGBled() (PWM auf Timer1) darf daneben nicht
// benutzt werden, digitalWriteRGB() dagegen schon.
//
// Stummschaltung: Arduboy2Audio::off() schaltet die Lautsprecher-Pins auf
// Eingang, dann bleibt der Player lautlos, ohne dass er davon wissen muss.
namespace Sound {
  void begin(uint24_t musicTable, uint8_t musicCount);
  void play(uint8_t track);  // NONE8 = stop
  void stop();
  void update();             // einmal pro Frame, außerhalb anderer FX-Lesevorgänge
  bool playing();
}
