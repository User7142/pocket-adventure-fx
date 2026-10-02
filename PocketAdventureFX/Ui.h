#pragma once

#include "Common.h"

// Controls and rendering on top of the game world.
//
// In-game controls:
//   D-pad        move cursor (accelerates when held)
//   A            execute sentence (“Walk to” or the selected verb)
//   B            menu: verbs and inventory
// While text is shown: A skips. In a dialogue: up/down, A chooses.
namespace Ui {
  void reset();
  void update();  // input; starts scripts if needed
  void draw();
  uint8_t hovered();  // object under the cursor or NONE8

  // Speech text (from the script). Expires after a time based on its length, or via A.
  void say(uint8_t actor, uint24_t text);
  bool talking();

  // Full screen (chapter card, from the script): stays until the music ends (without
  // music 3 s) or A is pressed – like skipping in the original.
  void card(uint24_t image, uint24_t grey, uint8_t music);  // grey: greyscale version|NONE24
  bool showingCard();
  void drawCard();

  // Flash (from the script, e.g. the Voodoo Lady's vision): the display
  // blinks inverted for `frames` frames, in a 4-frame cycle. inverted() says
  // whether it should currently be inverted; the sketch sends that to the display
  // while it is selected for the image transfer.
  void flash(uint8_t frames);
  bool inverted();

  // Dialogue choice (from the script): beginChoice, addChoice per visible option,
  // then ask – only from then on is the choice active (false: no option visible).
  void beginChoice();
  void addChoice(uint24_t text, uint24_t target);
  bool ask();
  bool choosing();
}
