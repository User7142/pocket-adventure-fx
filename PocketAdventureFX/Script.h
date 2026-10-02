#pragma once

#include "Common.h"

// Bytecode interpreter for the scripts from game.adv (opcodes: gamedata.h).
//
// At most one script runs at any time. While it runs, input is
// locked – so every script is a small cutscene. Blocking
// commands (say, walk, wait, choose) pause it until the world or the
// UI reports the event.
namespace Script {
  void start(uint24_t address);
  bool running();
  void update();                  // once per frame
  void chosen(uint24_t target);   // UI: dialogue option chosen
  void stopRoutine();             // stop all background routines (new game)
}
