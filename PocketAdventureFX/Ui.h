#pragma once

#include "Common.h"

// Bedienung und Darstellung über der Spielwelt.
//
// Steuerung im Spiel:
//   Steuerkreuz  Cursor bewegen (beschleunigt beim Halten)
//   A            Satz ausführen („Gehe zu“ oder das gewählte Verb)
//   B            Menü: Verben und Inventar
// Während Text läuft: A überspringt. Im Dialog: hoch/runter, A wählt.
namespace Ui {
  void reset();
  void update();  // Eingabe; startet ggf. Skripte
  void draw();
  uint8_t hovered();  // Objekt unter dem Mauszeiger oder NONE8

  // Sprechtext (vom Skript). Läuft nach Textlänge ab oder per A.
  void say(uint8_t actor, uint24_t text);
  bool talking();

  // Vollbild (Kapitelkarte, vom Skript): steht, bis die Musik endet (ohne
  // Musik 3 s) oder A gedrückt wird – wie das Überspringen im Original.
  void card(uint24_t image, uint8_t music);
  bool showingCard();
  void drawCard();

  // Blitz (vom Skript, z. B. die Vision der Voodoo-Lady): das Display
  // blinkt frames lang invertiert, im Takt von 4 Frames.
  void flash(uint8_t frames);

  // Dialogauswahl (vom Skript): beginChoice, addChoice je sichtbare Option,
  // dann ask – erst ab da gilt die Auswahl (false: keine Option sichtbar).
  void beginChoice();
  void addChoice(uint24_t text, uint24_t target);
  bool ask();
  bool choosing();
}
