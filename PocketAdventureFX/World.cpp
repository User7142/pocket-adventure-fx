#include "World.h"

namespace World {
  GameHeader header;
  ActorState actors[ACTOR_COUNT];
  uint8_t objects[OBJECT_COUNT];
  uint8_t inventory[MAX_INVENTORY];
  uint8_t inventoryCount;
  uint8_t room = NONE8;
  RoomRec roomRec;
  int16_t scrollX;
  int16_t scrollY;
  uint8_t talker = NONE8;
  char strings[STRING_SLOTS][STRING_SIZE];
  uint8_t vars[VAR_COUNT];
}

namespace {
  uint8_t flags[FLAG_BYTES];

  // Laufgeschwindigkeit pro Frame in 1/16 px: waagerecht 1 px, senkrecht ½ px
  // (die Räume sind perspektivisch gestaucht, wie im Original).
  constexpr int16_t SPEED_X = 16;
  constexpr int16_t SPEED_Y = 8;

  // --- Geometrie der Laufflächen (SCUMM-Walkboxes) ---
  //
  // Eine Box ist ein konvexes Viereck, das zu einer Linie oder einem Punkt
  // entarten darf (Treppen und schmale Wege im Original). Liegt das Ziel in
  // einer anderen Box, liefert die vom Compiler berechnete Matrix die nächste
  // Box auf dem Weg; der Actor läuft zum nächstgelegenen Punkt darin.

  struct Pt {
    int16_t x, y;
    bool operator==(const Pt& o) const { return x == o.x && y == o.y; }
  };

  Pt corner(const BoxRec& b, uint8_t i) {
    switch (i) {
      case 0: return {int16_t(b.ulx), b.uly};
      case 1: return {int16_t(b.urx), b.ury};
      case 2: return {int16_t(b.lrx), b.lry};
      default: return {int16_t(b.llx), b.lly};
    }
  }

  int32_t cross(Pt o, Pt a, Pt b) {
    return int32_t(a.x - o.x) * (b.y - o.y) - int32_t(a.y - o.y) * (b.x - o.x);
  }

  int32_t dist2(Pt a, Pt b) {
    int32_t dx = a.x - b.x, dy = a.y - b.y;
    return dx * dx + dy * dy;
  }

  // Punkt im Inneren (Rand eingeschlossen). Entartete Boxen haben kein
  // Inneres; für sie zählt nur der Rand, siehe closestInBox().
  bool inside(const BoxRec& b, Pt p) {
    int8_t sign = 0;
    uint8_t edges = 0;
    for (uint8_t i = 0; i < 4; ++i) {
      Pt a = corner(b, i), c = corner(b, (i + 1) & 3);
      if (a == c) continue;
      ++edges;
      int32_t v = cross(a, c, p);
      if (!v) continue;
      int8_t s = v > 0 ? 1 : -1;
      if (sign && s != sign) return false;
      sign = s;
    }
    return edges >= 3 && sign != 0;
  }

  Pt closestOnSegment(Pt a, Pt b, Pt p) {
    int32_t dx = b.x - a.x, dy = b.y - a.y;
    int32_t len2 = dx * dx + dy * dy;
    if (!len2) return a;
    int32_t t = int32_t(p.x - a.x) * dx + int32_t(p.y - a.y) * dy;
    if (t <= 0) return a;
    if (t >= len2) return b;
    return {int16_t(a.x + (dx * t + len2 / 2) / len2), int16_t(a.y + (dy * t + len2 / 2) / len2)};
  }

  // Laufflächen bleiben im FX-Flash (die Karte von Mêlée hat 46, im RAM wären
  // das über 500 Byte); eine Box zu lesen dauert nur Mikrosekunden.
  BoxRec readBox(uint8_t i) {
    BoxRec b;
    FX::readDataObject(World::roomRec.boxes + uint24_t(i) * sizeof(BoxRec), b);
    return b;
  }

  Pt closestInBox(uint8_t box, Pt p) {
    const BoxRec b = readBox(box);
    if (inside(b, p)) return p;
    Pt best = corner(b, 0);
    int32_t bestDist = dist2(best, p);
    for (uint8_t i = 0; i < 4; ++i) {
      Pt c = closestOnSegment(corner(b, i), corner(b, (i + 1) & 3), p);
      int32_t d = dist2(c, p);
      if (d < bestDist) {
        bestDist = d;
        best = c;
      }
    }
    return best;
  }

  // Nächste Lauffläche zu p; out erhält den nächsten begehbaren Punkt.
  uint8_t nearestBox(Pt p, Pt& out) {
    uint8_t best = 0;
    int32_t bestDist = INT32_MAX;
    for (uint8_t i = 0; i < World::roomRec.boxCount; ++i) {
      Pt c = closestInBox(i, p);
      int32_t d = dist2(c, p);
      if (d < bestDist) {
        bestDist = d;
        best = i;
        out = c;
      }
    }
    return best;
  }

  uint8_t nextBoxOnPath(uint8_t from, uint8_t to) {
    uint8_t v;
    FX::readDataObject(World::roomRec.matrix + uint24_t(from) * World::roomRec.boxCount + to, v);
    return v;
  }

  Pt position(const ActorState& s) {
    return {int16_t(s.x >> SUBPIXEL_SHIFT), int16_t(s.y >> SUBPIXEL_SHIFT)};
  }

  // Bestimmt den nächsten Wegpunkt. Die Schleife wechselt höchstens ein paar
  // Mal die Box, wenn der Actor schon auf der Grenze zur nächsten steht.
  void plan(ActorState& s) {
    for (uint8_t guard = 0; guard < MAX_WALKBOXES + 1; ++guard) {
      if (s.box == s.targetBox) {
        s.wayX = s.targetX;
        s.wayY = s.targetY;
        s.nextBox = s.targetBox;
        return;
      }
      Pt pos = position(s);
      uint8_t next = nextBoxOnPath(s.box, s.targetBox);
      if (next == NONE8) {
        // Ziel unerreichbar: so nah wie möglich auf der eigenen Fläche.
        Pt c = closestInBox(s.box, {int16_t(s.targetX), int16_t(s.targetY)});
        s.targetX = c.x;
        s.targetY = c.y;
        s.targetBox = s.box;
        continue;
      }
      Pt gate = closestInBox(next, pos);
      if (gate == pos) {
        s.box = next;
        continue;
      }
      s.wayX = gate.x;
      s.wayY = gate.y;
      s.nextBox = next;
      return;
    }
    s.walking = false;
  }

  void stepActor(ActorState& s) {
    int16_t dx = int16_t(s.wayX << SUBPIXEL_SHIFT) - int16_t(s.x);
    int16_t dy = int16_t(s.wayY << SUBPIXEL_SHIFT) - int16_t(s.y);
    if (!dx && !dy) {
      s.box = s.nextBox;
      if (s.wayX == s.targetX && s.wayY == s.targetY && s.box == s.targetBox) {
        s.walking = false;
      } else {
        plan(s);
      }
      return;
    }
    uint16_t ax = dx < 0 ? -dx : dx, ay = dy < 0 ? -dy : dy;
    uint16_t framesX = (ax + SPEED_X - 1) / SPEED_X;
    uint16_t framesY = (ay + SPEED_Y - 1) / SPEED_Y;
    int16_t frames = framesX > framesY ? framesX : framesY;
    s.x += dx / frames;
    s.y += dy / frames;
    if (ax >= ay / 2 && dx) s.dir = dx > 0 ? DIR_RIGHT : DIR_LEFT;
    ++s.walkPhase;
  }

  void readPlace(uint8_t i, PlaceRec& p) {
    FX::readDataObject(World::roomRec.places + uint24_t(i) * sizeof(PlaceRec), p);
  }

  void spriteSize(uint24_t sprite, uint16_t& w, uint16_t& h) {
    FX::seekData(sprite);
    w = FX::readPendingUInt16();  // Bild-Header ist big-endian (FX-Format)
    h = FX::readPendingLastUInt16();
  }

  // Größe (255 = voll) an der Position des Actors: aus seiner Lauffläche,
  // linear zwischen oberer und unterer Kante (Skalierung des Originals).
  uint8_t scaleAt(const ActorState& s) {
    if (!World::roomRec.boxCount) return 255;
    const BoxRec b = readBox(s.box);
    int16_t top = b.uly < b.ury ? b.uly : b.ury;
    int16_t bottom = b.lly > b.lry ? b.lly : b.lry;
    int16_t y = s.y >> SUBPIXEL_SHIFT;
    if (bottom <= top || y <= top) return b.scaleTop;
    if (y >= bottom) return b.scaleBottom;
    return b.scaleTop + int16_t(int16_t(b.scaleBottom) - b.scaleTop) * (y - top) / (bottom - top);
  }

  // Sprite für die Größe: Figuren mit Tiefe haben kleinere Stufen (advc.py:
  // u8 Anzahl, je Stufe u8 Schwelle + u24 Sprite, absteigend).
  uint24_t actorSprite(const ActorState& s, const ActorRec& rec) {
    if (rec.depth == NONE24) return rec.sprite;
    uint8_t scale = scaleAt(s);
    uint24_t sprite = rec.sprite;
    uint8_t n;
    FX::readDataObject(rec.depth, n);
    for (uint8_t i = 0; i < n; ++i) {
      uint8_t level[4];
      FX::readDataBytes(rec.depth + 1 + uint24_t(i) * 4, level, 4);
      if (scale >= level[0]) break;
      sprite = le24(level + 1);
    }
    return sprite;
  }

  void drawActor(uint8_t a) {
    const ActorState& s = World::actors[a];
    ActorRec rec;
    FX::readDataObject(World::header.actors + uint24_t(s.look) * sizeof(ActorRec), rec);

    bool talking = World::talker == a && (arduboy.frameCount & 8);
    uint8_t frame;
    if (s.walking) {
      frame = rec.walkFirst + (s.walkPhase / 6) % rec.walkCount;
    } else if (s.dir == DIR_FRONT) {
      frame = talking ? rec.frontTalk : rec.front;
    } else {
      frame = talking ? rec.talk : rec.stand;
    }

    uint24_t sprite = actorSprite(s, rec);
    uint16_t w, h;
    spriteSize(sprite, w, h);
    uint8_t mode = dbmMasked;
    if (s.dir == DIR_LEFT) mode |= dbmFlip;
    Pt p = position(s);
    FX::drawBitmap(p.x - int16_t(w / 2) - World::scrollX, p.y - int16_t(h) + 1 - World::scrollY, sprite, frame, mode);
  }

  // Kamera: folgt der Spielfigur, außer ein Skript schwenkt sie (pan).
  int16_t panX = -1;

  int16_t cameraTarget() {
    int16_t maxScroll = int16_t(World::roomRec.width) - WIDTH;
    int16_t target = (panX >= 0 ? panX : position(World::actors[PLAYER]).x) - WIDTH / 2;
    if (target < 0) target = 0;
    if (target > maxScroll) target = maxScroll;
    return target;
  }

  // Senkrecht nur in Räumen, die höher als das Display sind (die Karte).
  int16_t cameraTargetY() {
    int16_t maxScroll = int16_t(World::roomRec.height) - HEIGHT;
    int16_t target = position(World::actors[PLAYER]).y - HEIGHT / 2;
    if (target > maxScroll) target = maxScroll;
    if (target < 0) target = 0;
    return target;
  }

  void follow(int16_t& scroll, int16_t target, bool snap) {
    if (snap) {
      scroll = target;
    } else if (scroll < target) {
      scroll += (target - scroll > 1) ? 2 : 1;
    } else if (scroll > target) {
      scroll -= (scroll - target > 1) ? 2 : 1;
    }
  }

  void updateCamera(bool snap) {
    follow(World::scrollX, cameraTarget(), snap);
    follow(World::scrollY, cameraTargetY(), snap);
  }
}

namespace {
  LangDir langDir;

  LangEntry langEntry(uint8_t i) {
    LangEntry e;
    FX::readDataObject(sizeof(LangDir) + uint24_t(i) * sizeof(LangEntry), e);
    return e;
  }
}

bool World::begin() {
  FX::readDataObject(0, langDir);
  return langDir.magic == GAME_MAGIC && langDir.buildId == GAME_BUILD_ID && langDir.count > 0;
}

uint8_t World::languageCount() {
  return langDir.count;
}

uint24_t World::languageName(uint8_t i) {
  return langEntry(i).name;
}

void World::selectLanguage(uint8_t i) {
  FX::readDataObject(langEntry(i).header, header);
}

void World::reset() {
  memset(actors, 0, sizeof(actors));
  for (uint8_t a = 0; a < ACTOR_COUNT; ++a) {
    actors[a].room = NONE8;
    actors[a].look = a;
  }
  memset(objects, 0, sizeof(objects));
  memset(flags, 0, sizeof(flags));
  memset(strings, 0, sizeof(strings));
  memset(vars, 0, sizeof(vars));
  inventoryCount = 0;
  room = NONE8;
  talker = NONE8;
  scrollX = 0;
  scrollY = 0;
}

void World::loadRoom(uint8_t r) {
  if (r >= header.roomCount) return;
  room = r;
  panX = -1;
  FX::readDataObject(header.rooms + uint24_t(r) * sizeof(RoomRec), roomRec);
  for (ActorState& s : actors) s.walking = false;
  updateCamera(true);
}

void World::update() {
  for (ActorState& s : actors) {
    if (s.walking && s.room == room) stepActor(s);
  }
  updateCamera(false);
}

void World::draw() {
  FX::drawBitmap(-scrollX, -scrollY, roomRec.background, 0, dbmNormal);

  for (uint8_t i = 0; i < roomRec.placeCount; ++i) {
    PlaceRec p;
    readPlace(i, p);
    // Dekoration (object == NONE8): nur Bild, immer Zustand 0.
    uint8_t flags = p.object == NONE8 ? 0 : objects[p.object];
    if (p.image == NONE24 || (flags & (OBJ_OWNED | OBJ_HIDDEN))) continue;
    uint8_t frame = (flags & OBJ_STATE) * p.frames;
    if (p.frames > 1) frame += (arduboy.frameCount / p.speed) % p.frames;
    FX::drawBitmap(int16_t(p.x) - scrollX, int16_t(p.y) - scrollY, p.image, frame, dbmMasked);
  }

  // Actors nach Fußlinie sortiert: weiter vorne = später gezeichnet.
  uint8_t order[ACTOR_COUNT];
  uint8_t n = 0;
  for (uint8_t a = 0; a < ACTOR_COUNT; ++a) {
    if (actors[a].room != room) continue;
    uint8_t i = n++;
    while (i && actors[order[i - 1]].y > actors[a].y) {
      order[i] = order[i - 1];
      --i;
    }
    order[i] = a;
  }
  for (uint8_t i = 0; i < n; ++i) drawActor(order[i]);
}

void World::put(uint8_t actor, uint16_t x, uint8_t y) {
  if (actor >= ACTOR_COUNT) return;
  ActorState& s = actors[actor];
  s.x = x << SUBPIXEL_SHIFT;
  s.y = y << SUBPIXEL_SHIFT;
  s.room = room;
  s.walking = false;
  if (actor == PLAYER) panX = -1;
  Pt snapped;
  s.box = s.targetBox = s.nextBox = nearestBox({int16_t(x), int16_t(y)}, snapped);
  if (actor == PLAYER) updateCamera(true);
}

void World::costume(uint8_t actor, uint8_t look) {
  if (actor < ACTOR_COUNT && look < ACTOR_COUNT) actors[actor].look = look;
}

void World::pan(uint16_t x) {
  panX = x;
}

bool World::panning() {
  return panX >= 0 && scrollX != cameraTarget();
}

void World::remove(uint8_t actor) {
  if (actor >= ACTOR_COUNT) return;
  actors[actor].room = NONE8;
  actors[actor].walking = false;
  if (talker == actor) talker = NONE8;
}

void World::walkTo(uint8_t actor, uint16_t x, uint8_t y) {
  if (actor >= ACTOR_COUNT || !roomRec.boxCount) return;
  ActorState& s = actors[actor];
  Pt target;
  s.targetBox = nearestBox({int16_t(x), int16_t(y)}, target);
  s.targetX = target.x;
  s.targetY = target.y;
  s.walking = true;
  plan(s);
}

bool World::isWalking(uint8_t actor) {
  return actor < ACTOR_COUNT && actors[actor].walking;
}

void World::face(uint8_t actor, uint8_t dir) {
  if (actor < ACTOR_COUNT) actors[actor].dir = dir;
}

int16_t World::screenX(uint8_t actor) {
  if (actor >= ACTOR_COUNT || actors[actor].room != room) return WIDTH / 2;
  return position(actors[actor]).x - scrollX;
}

bool World::flag(uint8_t f) {
  return flags[f >> 3] & (1 << (f & 7));
}

void World::setFlag(uint8_t f, bool on) {
  if ((f >> 3) >= FLAG_BYTES) return;
  if (on) flags[f >> 3] |= 1 << (f & 7);
  else flags[f >> 3] &= ~(1 << (f & 7));
}

bool World::has(uint8_t object) {
  return object < OBJECT_COUNT && (objects[object] & OBJ_OWNED);
}

void World::pickup(uint8_t object) {
  if (object >= OBJECT_COUNT || has(object) || inventoryCount >= MAX_INVENTORY) return;
  objects[object] |= OBJ_OWNED;
  inventory[inventoryCount++] = object;
}

void World::lose(uint8_t object) {
  if (!has(object)) return;
  objects[object] = (objects[object] & ~OBJ_OWNED) | OBJ_HIDDEN;
  uint8_t j = 0;
  for (uint8_t i = 0; i < inventoryCount; ++i) {
    if (inventory[i] != object) inventory[j++] = inventory[i];
  }
  inventoryCount = j;
}

void World::setState(uint8_t object, uint8_t state) {
  if (object < OBJECT_COUNT) objects[object] = (objects[object] & ~OBJ_STATE) | (state & OBJ_STATE);
}

void World::setHidden(uint8_t object, bool hidden) {
  if (object >= OBJECT_COUNT) return;
  if (hidden) objects[object] |= OBJ_HIDDEN;
  else objects[object] &= ~OBJ_HIDDEN;
}

uint8_t World::hitTest(int16_t x, int16_t y) {
  // Figuren mit eigenem Objekt zuerst: Sie werden über der Kulisse gezeichnet.
  for (uint8_t a = 0; a < ACTOR_COUNT; ++a) {
    if (actors[a].room != room) continue;
    ActorRec rec;
    FX::readDataObject(header.actors + uint24_t(a) * sizeof(ActorRec), rec);
    if (rec.object == NONE8 || (objects[rec.object] & (OBJ_OWNED | OBJ_HIDDEN))) continue;
    uint8_t object = rec.object;
    if (actors[a].look != a)
      FX::readDataObject(header.actors + uint24_t(actors[a].look) * sizeof(ActorRec), rec);
    uint16_t w, h;
    spriteSize(actorSprite(actors[a], rec), w, h);
    Pt p = position(actors[a]);
    int16_t left = p.x - int16_t(w / 2);
    if (x >= left && x < left + int16_t(w) && y > p.y - int16_t(h) && y <= p.y) return object;
  }
  // Rückwärts: später platzierte Objekte liegen oben.
  for (uint8_t i = roomRec.placeCount; i-- > 0;) {
    PlaceRec p;
    readPlace(i, p);
    if (p.object == NONE8 || (objects[p.object] & (OBJ_OWNED | OBJ_HIDDEN))) continue;
    if (x >= int16_t(p.x) && x < int16_t(p.x) + p.w && y >= p.y && y < p.y + p.h) return p.object;
  }
  return NONE8;
}

bool World::findPlace(uint8_t object, PlaceRec& out) {
  for (uint8_t i = 0; i < roomRec.placeCount; ++i) {
    readPlace(i, out);
    if (out.object == object) return true;
  }
  // Figur als Objekt: neben sie laufen, auf der Seite, von der die
  // Spielfigur kommt, und ihr zugewandt stehen bleiben.
  constexpr int16_t BESIDE = 14;
  for (uint8_t a = 0; a < ACTOR_COUNT; ++a) {
    if (a == PLAYER || actors[a].room != room) continue;
    ActorRec rec;
    FX::readDataObject(header.actors + uint24_t(a) * sizeof(ActorRec), rec);
    if (rec.object != object) continue;
    Pt target = position(actors[a]);
    bool fromLeft = position(actors[PLAYER]).x < target.x;
    int16_t wx = target.x + (fromLeft ? -BESIDE : BESIDE);
    out.object = object;
    out.walkX = wx < 0 ? 0 : wx;
    out.walkY = target.y;
    out.face = fromLeft ? DIR_RIGHT : DIR_LEFT;
    return true;
  }
  return false;
}

uint8_t World::readString(uint24_t address, char* buffer, uint8_t size) {
  uint8_t n = 0;
  FX::seekData(address);
  for (;;) {
    char c = FX::readPendingUInt8();
    if (!c || n == size - 1) break;
    if (c == STRING_VAR) {
      const char* v = strings[uint8_t(FX::readPendingUInt8()) - 1];
      for (; *v && n < size - 1; ++v)
        if (*v != '@') buffer[n++] = *v;
      continue;
    }
    if (c == INT_VAR) {
      char digits[3];
      uint8_t v = vars[uint8_t(FX::readPendingUInt8()) - 1], k = 0;
      do digits[k++] = '0' + v % 10; while ((v /= 10));
      while (k && n < size - 1) buffer[n++] = digits[--k];
      continue;
    }
    buffer[n++] = c;
  }
  FX::readEnd();
  buffer[n] = 0;
  return n;
}
