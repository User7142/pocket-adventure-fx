#pragma once

#include "Common.h"

// Monophonic PC-speaker player for tracks in the FX flash.
//
// The notes of the title theme mostly last only 10–18 ms, which is
// shorter than a frame (16.7 ms). Timing therefore does not run via the
// frame loop but via a 1 kHz interrupt on Timer1; Timer3
// generates the pitch directly on the speaker pin in CTC mode.
//
// The interrupt only reads from a RAM ring buffer. It is refilled
// from the FX flash in update(), i.e. in the main program – so the
// interrupt and the SPI accesses to flash and display never get in each other's way.
//
// Occupies Timer1: Arduboy2::setRGBled() (PWM on Timer1) must not be
// used alongside it, digitalWriteRGB() however may.
//
// Muting: Arduboy2Audio::off() switches the speaker pins to
// input, so the player stays silent without having to know about it.
namespace Sound {
  void begin(uint24_t musicTable, uint8_t musicCount);
  void play(uint8_t track);  // NONE8 = stop
  void stop();
  void update();             // once per frame, outside of other FX reads
  bool playing();
}
