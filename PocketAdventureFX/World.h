#pragma once

#include "Common.h"

// Festkomma für Actor-Positionen: 1/16 Pixel, damit diagonale Wege gleichmäßig werden.
constexpr uint8_t SUBPIXEL_SHIFT = 4;

// Laufzeitzustand eines Actors. Alles Statische (Name, Sprite, Frames)
// steht im ActorRec im FX-Flash und wird bei Bedarf gelesen.
struct ActorState {
  uint16_t x, y;            // Fußpunkt in Raumkoordinaten, 1/16 px
  uint16_t targetX;         // Ziel (px), bereits auf eine Lauffläche gezogen
  uint8_t targetY;
  uint16_t wayX;            // nächster Wegpunkt (px)
  uint8_t wayY;
  uint8_t box;              // Lauffläche, auf der der Actor steht
  uint8_t nextBox;          // Lauffläche am Wegpunkt
  uint8_t targetBox;
  uint8_t room;             // NONE8 = in keinem Raum
  uint8_t dir;
  uint8_t walkPhase;
  bool walking;
  uint8_t look;             // Actor-Definition, deren Grafik gezeigt wird (costume)
  // Zeichenstand, je Logikschritt berechnet (World::prepare): Sprite der
  // Größenstufe, Frame, Größe. draw() läuft je Ebene, dreimal so oft, und
  // soll nur noch zeichnen.
  uint24_t sprite;
  uint8_t frame, w, h;
  bool layered;  // sprite ist die Graustufen-Fassung (3 Frames je Frame)
};

// Objektzustand: ein Byte pro Objekt
constexpr uint8_t OBJ_OWNED = 0x80;   // im Inventar
constexpr uint8_t OBJ_HIDDEN = 0x40;  // im Raum ausgeblendet (auch: verbraucht)
constexpr uint8_t OBJ_STATE = 0x3F;   // Bildzustand (Framegruppe)

// Spielwelt: Header, Raum, Actors, Objekte, Flags, Inventar.
// Der gesamte veränderliche Spielstand liegt hier im RAM (wenige hundert Bytes),
// alles andere wird aus dem FX-Flash gestreamt.
namespace World {
  extern GameHeader header;
  extern ActorState actors[ACTOR_COUNT];
  extern uint8_t objects[OBJECT_COUNT];
  extern uint8_t inventory[MAX_INVENTORY];
  extern uint8_t inventoryCount;
  extern uint8_t room;
  extern RoomRec roomRec;
  extern int16_t scrollX;
  extern int16_t scrollY;  // nur in Räumen, die höher als das Display sind
  extern uint8_t talker;  // Actor, der gerade spricht, oder NONE8
  // String-Variablen (string … original <nr>), eingesetzt in Texte mit
  // STRING_VAR + Platz + 1; '@' füllt nur auf und wird nicht ausgegeben.
  extern char strings[STRING_SLOTS][STRING_SIZE];
  extern uint8_t vars[VAR_COUNT];  // Zahlenvariablen der Spielbeschreibung (let/add)

  // Sprachverzeichnis lesen; false, wenn FX-Daten fehlen oder nicht zum Sketch passen.
  bool begin();
  uint8_t languageCount();
  uint24_t languageName(uint8_t i);   // String im FX-Flash
  void selectLanguage(uint8_t i);     // lädt den Header dieser Sprache
  void reset();  // Spielstand für ein neues Spiel
  void loadRoom(uint8_t r);
  void update();
  void prepare();  // Zeichenstand der Actors nach einem Logikschritt
  void draw();

  void put(uint8_t actor, uint16_t x, uint8_t y);
  void walkTo(uint8_t actor, uint16_t x, uint8_t y);
  void remove(uint8_t actor);       // auch die Spielfigur (z. B. hinter einer Tür)
  void costume(uint8_t actor, uint8_t look);  // mit der Grafik einer anderen Actor-Definition zeigen
  void pan(uint16_t x);              // Kamera zu x schwenken, bis die Spielfigur wieder gesetzt wird
  bool panning();
  bool isWalking(uint8_t actor);
  void face(uint8_t actor, uint8_t dir);
  int16_t screenX(uint8_t actor);

  bool flag(uint8_t f);
  void setFlag(uint8_t f, bool on);
  bool has(uint8_t object);
  void pickup(uint8_t object);
  void lose(uint8_t object);
  void setState(uint8_t object, uint8_t state);
  void setHidden(uint8_t object, bool hidden);

  uint8_t hitTest(int16_t x, int16_t y);  // Raumkoordinaten → Objekt oder NONE8
  bool findPlace(uint8_t object, PlaceRec& out);

  // Liest einen nullterminierten String aus dem FX-Flash (gekürzt auf
  // size-1) und setzt dabei String-Variablen ein.
  uint8_t readString(uint24_t address, char* buffer, uint8_t size);
}
