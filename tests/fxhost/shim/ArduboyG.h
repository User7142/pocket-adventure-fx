// Host stand-in for ArduboyG: three planes in rotation as on the device.
// waitForNextPlane() takes over the drawn plane (host::displayed) and
// switches to the next; after a full cycle needsUpdate() signals
// exactly one step of game logic. The test bench therefore calls loop() three
// times per frame (harness.cpp: step).
#pragma once

#include "Arduboy2.h"

// Colours as in ArduboyG: level 0–3, white is lit in all three planes.
#undef BLACK
#undef WHITE
constexpr uint8_t BLACK = 0, DARK_GREY = 1, LIGHT_GREY = 2, WHITE = 3;

enum class ABG_Mode : uint8_t { L4_Contrast, L4_Triplane, L3 };

namespace host {
  constexpr uint8_t PLANES = 3;
  void displayed(uint8_t plane);  // plane done: take over the image, clear the buffer
}

template <ABG_Mode MODE>
class ArduboyG_Config : public Arduboy2 {
 public:
  void startGray() {}
  void setUpdateHz(uint8_t) {}
  void waitForNextPlane() {
    host::displayed(plane);
    plane = (plane + 1) % host::PLANES;
    if (plane == 0) pending = true;
  }
  bool needsUpdate() {
    bool due = pending;
    pending = false;
    return due;
  }
  static uint8_t currentPlane() { return plane; }
  // colour in the current plane (L4_Triplane: lit if the level is above it)
  static uint8_t color(uint8_t c) { return c > plane; }

 private:
  static inline uint8_t plane = 0;
  bool pending = false;
};
