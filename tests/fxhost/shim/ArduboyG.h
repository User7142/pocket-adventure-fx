// Host-Nachbildung von ArduboyG: drei Ebenen im Wechsel wie auf dem Gerät.
// waitForNextPlane() übernimmt die gezeichnete Ebene (host::displayed) und
// schaltet zur nächsten; nach einem vollen Durchlauf meldet needsUpdate()
// genau einen Schritt Spiellogik. Der Prüfstand ruft loop() darum dreimal je
// Frame auf (harness.cpp: step).
#pragma once

#include "Arduboy2.h"

// Farben wie in ArduboyG: Stufe 0–3, Weiß ist in allen drei Ebenen hell.
#undef BLACK
#undef WHITE
constexpr uint8_t BLACK = 0, DARK_GREY = 1, LIGHT_GREY = 2, WHITE = 3;

enum class ABG_Mode : uint8_t { L4_Contrast, L4_Triplane, L3 };

namespace host {
  constexpr uint8_t PLANES = 3;
  void displayed(uint8_t plane);  // Ebene fertig: Bild übernehmen, Puffer leeren
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
  // Farbe in der aktuellen Ebene (L4_Triplane: hell, wenn die Stufe darüber liegt)
  static uint8_t color(uint8_t c) { return c > plane; }

 private:
  static inline uint8_t plane = 0;
  bool pending = false;
};
