#pragma once

#include "Common.h"

// Bytecode-Interpreter für die Skripte aus game.adv (Opcodes: gamedata.h).
//
// Es läuft immer höchstens ein Skript. Solange es läuft, ist die Eingabe
// gesperrt – jedes Skript ist also eine kleine Zwischensequenz. Blockierende
// Befehle (say, walk, wait, choose) halten es an, bis die Welt oder die
// Oberfläche das Ereignis melden.
namespace Script {
  void start(uint24_t address);
  bool running();
  void update();                  // einmal pro Frame
  void chosen(uint24_t target);   // Oberfläche: Dialogoption gewählt
  void stopRoutine();             // alle Hintergrundabläufe beenden (neues Spiel)
}
