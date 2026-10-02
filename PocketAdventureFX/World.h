#pragma once

#include "Common.h"

// Fixed point for actor positions: 1/16 pixel, so that diagonal paths are smooth.
constexpr uint8_t SUBPIXEL_SHIFT = 4;

// Runtime state of an actor. Everything static (name, sprite, frames)
// lives in the ActorRec in the FX flash and is read on demand.
struct ActorState {
  uint16_t x, y;            // foot point in room coordinates, 1/16 px
  uint16_t targetX;         // target (px), already snapped onto a walk box
  uint8_t targetY;
  uint16_t wayX;            // next waypoint (px)
  uint8_t wayY;
  uint8_t box;              // walk box the actor is standing on
  uint8_t nextBox;          // walk box at the waypoint
  uint8_t targetBox;
  uint8_t room;             // NONE8 = in no room
  uint8_t dir;
  uint8_t walkPhase;
  bool walking;
  uint8_t look;             // actor definition whose graphics are shown (costume)
  // Draw state, computed per logic step (World::prepare): sprite of the
  // size level, frame, size. draw() runs per plane, three times as often, and
  // should only draw.
  uint24_t sprite;
  uint8_t frame, w, h;
  bool layered;  // sprite is the greyscale version (3 frames per frame)
};

// Object state: one byte per object
constexpr uint8_t OBJ_OWNED = 0x80;   // in the inventory
constexpr uint8_t OBJ_HIDDEN = 0x40;  // hidden in the room (also: used up)
constexpr uint8_t OBJ_STATE = 0x3F;   // image state (frame group)

// Game world: header, room, actors, objects, flags, inventory.
// The entire mutable game state lives here in RAM (a few hundred bytes),
// everything else is streamed from the FX flash.
namespace World {
  extern GameHeader header;
  extern ActorState actors[ACTOR_COUNT];
  extern uint8_t objects[OBJECT_COUNT];
  extern uint8_t inventory[MAX_INVENTORY];
  extern uint8_t inventoryCount;
  extern uint8_t room;
  extern RoomRec roomRec;
  extern int16_t scrollX;
  extern int16_t scrollY;  // only in rooms taller than the display
  extern uint8_t talker;  // actor currently speaking, or NONE8
  // String variables (string … original <nr>), inserted into texts with
  // STRING_VAR + slot + 1; '@' only pads and is not output.
  extern char strings[STRING_SLOTS][STRING_SIZE];
  extern uint8_t vars[VAR_COUNT];  // numeric variables of the game description (let/add)

  // Read the language directory; false if FX data is missing or does not match the sketch.
  bool begin();
  uint8_t languageCount();
  uint24_t languageName(uint8_t i);   // string in the FX flash
  void selectLanguage(uint8_t i);     // loads the header of this language
  void reset();  // game state for a new game
  void loadRoom(uint8_t r);
  void update();
  void prepare();  // draw state of the actors after a logic step
  void draw();

  void put(uint8_t actor, uint16_t x, uint8_t y);
  void walkTo(uint8_t actor, uint16_t x, uint8_t y);
  void remove(uint8_t actor);       // also the player character (e.g. behind a door)
  void costume(uint8_t actor, uint8_t look);  // show with the graphics of another actor definition
  void pan(uint16_t x);              // pan camera to x until the player character is placed again
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

  uint8_t hitTest(int16_t x, int16_t y);  // room coordinates → object or NONE8
  bool findPlace(uint8_t object, PlaceRec& out);

  // Reads a null-terminated string from the FX flash (truncated to
  // size-1), substituting string variables.
  uint8_t readString(uint24_t address, char* buffer, uint8_t size);
}
