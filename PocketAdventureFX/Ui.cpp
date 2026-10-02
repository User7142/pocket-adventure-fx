#include "Ui.h"
#include "Sound.h"
#include "Script.h"
#include "World.h"

namespace {
  constexpr uint8_t CHAR_W = 6;
  constexpr uint8_t LINE_H = 8;
  constexpr uint8_t BAR_H = 8;           // Satzzeile oben
  constexpr uint8_t MENU_COLS = 2;
  constexpr uint8_t MENU_NAME_CHARS = 10;
  constexpr uint8_t MENU_VERB_ROWS = (VERB_COUNT + MENU_COLS - 1) / MENU_COLS;
  constexpr uint8_t MENU_INV_TOP = MENU_VERB_ROWS * LINE_H + 1;
  constexpr uint8_t MENU_INV_LINE_H = 7;
  constexpr uint8_t MENU_INV_ROWS = (HEIGHT - MENU_INV_TOP) / MENU_INV_LINE_H;

  // --- Sprechtext ---
  char text[TEXT_BUFFER];  // Sprechblase oder voller Text der gewählten Option
  uint8_t textActor = NONE8;
  uint16_t textFrames;  // 0 = kein Text
  uint24_t cardImage = NONE24;
  bool cardMusic;
  uint8_t cardFrames;

  // --- Dialogauswahl ---
  uint24_t choiceText[MAX_OPTIONS];
  uint24_t choiceTarget[MAX_OPTIONS];
  uint8_t choiceCount;
  uint8_t choiceSel;
  uint8_t choiceTop;            // erste sichtbare Option (Liste scrollt)
  uint8_t choiceShown = NONE8;  // Option, deren voller Text in text[] steht
  uint8_t choiceRows;           // sichtbare Zeilen der Liste (je nach Länge des Texts)
  uint8_t flashFrames;          // Blitz: Restdauer
  bool choiceActive;

  // --- Satz ---
  uint8_t verb = 0;             // 0 = „Gehe zu“ (vom Compiler erzwungen)
  uint8_t firstObject = NONE8;  // gesetzt, solange das zweite Objekt fehlt
  uint8_t hover = NONE8;

  // Satz, der ausgeführt wird, sobald die Spielfigur angekommen ist
  bool pending;
  uint8_t pendingFace;
  uint24_t pendingScript;

  // --- Cursor und Menü ---
  int16_t cursorX = WIDTH / 2;
  int16_t cursorY = HEIGHT / 2;
  uint8_t holdFrames;
  bool menuOpen;
  uint8_t menuRow, menuCol, menuTop;

  VerbRec readVerb(uint8_t v) {
    VerbRec r;
    FX::readDataObject(World::header.verbs + uint24_t(v) * sizeof(VerbRec), r);
    return r;
  }

  uint24_t objectName(uint8_t o) {
    ObjectRec r;
    FX::readDataObject(World::header.objects + uint24_t(o) * sizeof(ObjectRec), r);
    return r.name;
  }

  // Skript für „verb object [other]“ aus der Verbtabelle des Objekts.
  // Mit any gilt auch „on other“ (VERB_ANY: alle übrigen Verben außer
  // „Gehe zu“, wie Verb 255 im Original), aber erst nach den genauen.
  uint24_t findHandler(uint8_t object, uint8_t v, uint8_t other, bool any = false) {
    ObjectRec r;
    FX::readDataObject(World::header.objects + uint24_t(object) * sizeof(ObjectRec), r);
    uint24_t fallback = NONE24;
    for (uint24_t a = r.verbs;; a += sizeof(VerbEntry)) {
      VerbEntry e;
      FX::readDataObject(a, e);
      if (e.verb == NONE8) return fallback;
      if (e.verb == v && e.other == other) return e.script;
      if (any && v != 0 && e.verb == VERB_ANY) fallback = e.script;
    }
  }

  void resetSentence() {
    verb = 0;
    firstObject = NONE8;
  }

  // Führt einen vollständigen Satz aus: passendes Skript suchen (in beiden
  // Richtungen, dann die Standardantwort des Verbs), vorher zum Objekt laufen.
  void execute(uint8_t v, uint8_t a, uint8_t b) {
    uint24_t script = findHandler(a, v, b);
    if (script == NONE24 && b != NONE8) script = findHandler(b, v, a);
    if (script == NONE24) script = findHandler(a, v, b, true);
    if (script == NONE24) script = readVerb(v).fallback;

    uint8_t target = (b != NONE8 && !World::has(b)) ? b : (!World::has(a) ? a : NONE8);
    PlaceRec p;
    if (target != NONE8 && World::findPlace(target, p) && p.walkX != WALK_DIRECT) {
      World::walkTo(PLAYER, p.walkX, p.walkY);
      pending = true;
      pendingFace = p.face;
      pendingScript = script;
    } else if (script != NONE24) {
      Script::start(script);
    }
    resetSentence();
  }

  // Ein Objekt wurde angeklickt (im Raum oder im Inventar).
  void pick(uint8_t object) {
    if (firstObject != NONE8) {
      execute(verb, firstObject, object);
      return;
    }
    // Zwei-Objekt-Verben („Benutze … mit …“) warten auf das zweite Objekt,
    // wenn es für das erste allein keine Antwort gibt.
    if (readVerb(verb).prep != NONE24 && findHandler(object, verb, NONE8) == NONE24) {
      firstObject = object;
      return;
    }
    execute(verb, object, NONE8);
  }

  void updateCursor() {
    int8_t dx = 0, dy = 0;
    if (arduboy.pressed(LEFT_BUTTON)) dx = -1;
    if (arduboy.pressed(RIGHT_BUTTON)) dx = 1;
    if (arduboy.pressed(UP_BUTTON)) dy = -1;
    if (arduboy.pressed(DOWN_BUTTON)) dy = 1;
    if (dx || dy) {
      if (holdFrames < 255) ++holdFrames;
    } else {
      holdFrames = 0;
    }
    uint8_t speed = holdFrames > 20 ? 2 : 1;
    cursorX += dx * speed;
    cursorY += dy * speed;
    if (cursorX < 0) cursorX = 0;
    if (cursorX >= WIDTH) cursorX = WIDTH - 1;
    if (cursorY < 0) cursorY = 0;
    if (cursorY >= HEIGHT) cursorY = HEIGHT - 1;
  }

  // --- Menü: Zeilen 0..MENU_VERB_ROWS-1 Verben, danach Inventar ---

  uint8_t menuItems(uint8_t row) {
    if (row < MENU_VERB_ROWS) {
      uint8_t first = row * MENU_COLS;
      return VERB_COUNT - first < MENU_COLS ? VERB_COUNT - first : MENU_COLS;
    }
    uint8_t first = (row - MENU_VERB_ROWS) * MENU_COLS;
    if (first >= World::inventoryCount) return 0;
    uint8_t left = World::inventoryCount - first;
    return left < MENU_COLS ? left : MENU_COLS;
  }

  void menuMove(int8_t dr, int8_t dc) {
    if (dc) {
      int8_t c = menuCol + dc;
      if (c >= 0 && c < menuItems(menuRow)) menuCol = c;
    }
    if (dr) {
      int8_t r = menuRow + dr;
      if (r >= 0 && menuItems(r)) {
        menuRow = r;
        if (menuCol >= menuItems(r)) menuCol = 0;
      }
    }
    // Inventar scrollt, damit die Auswahl sichtbar bleibt.
    if (menuRow >= MENU_VERB_ROWS) {
      uint8_t invRow = menuRow - MENU_VERB_ROWS;
      if (invRow < menuTop) menuTop = invRow;
      if (invRow >= menuTop + MENU_INV_ROWS) menuTop = invRow - MENU_INV_ROWS + 1;
    }
  }

  void menuSelect() {
    menuOpen = false;
    if (menuRow < MENU_VERB_ROWS) {
      verb = menuRow * MENU_COLS + menuCol;
      firstObject = NONE8;
      return;
    }
    uint8_t item = World::inventory[(menuRow - MENU_VERB_ROWS) * MENU_COLS + menuCol];
    if (verb == 0) {
      // Mit „Gehe zu“ auf ein Inventarobjekt: Verb aus der Spielbeschreibung.
      if (World::header.inventoryVerb == NONE8) return;
      verb = World::header.inventoryVerb;
    }
    pick(item);
  }

  // --- Zeichnen ---

  // Erste Zeile eines Strings aus dem FX-Flash, höchstens maxChars Zeichen.
  void printFx(uint24_t address, uint8_t maxChars) {
    char buf[TEXT_COLS + 2];
    if (maxChars > sizeof(buf) - 1) maxChars = sizeof(buf) - 1;
    World::readString(address, buf, maxChars + 1);
    char* nl = strchr(buf, '\n');
    if (nl) *nl = 0;
    arduboy.print(buf);
  }

  // Hängt „ Wort“ aus dem FX-Flash an den Satzpuffer an.
  uint8_t appendFx(char* buf, uint8_t len, uint8_t size, uint24_t address) {
    if (len && len < size - 1) buf[len++] = ' ';
    return len + World::readString(address, buf + len, size - len);
  }

  // Satzzeile: „Verb Objekt [prep Objekt]“. Passt der Satz nicht in eine
  // Zeile, wird sein Ende gezeigt – dort steht das Objekt unter dem Cursor.
  void drawSentence() {
    constexpr uint8_t COLS = WIDTH / CHAR_W;
    char buf[3 * (TEXT_COLS + 1) + 8];
    VerbRec v = readVerb(verb);
    uint8_t len = appendFx(buf, 0, sizeof(buf), v.name);
    uint8_t shown = firstObject != NONE8 ? firstObject : hover;
    if (shown != NONE8) len = appendFx(buf, len, sizeof(buf), objectName(shown));
    if (firstObject != NONE8 && v.prep != NONE24) {
      len = appendFx(buf, len, sizeof(buf), v.prep);
      if (hover != NONE8 && hover != firstObject) len = appendFx(buf, len, sizeof(buf), objectName(hover));
    }
    arduboy.fillRect(0, 0, WIDTH, BAR_H, BLACK);
    arduboy.setCursor(1, 0);
    arduboy.print(len > COLS ? buf + len - COLS : buf);
  }

  void drawMenuItem(uint8_t x, uint8_t y, uint24_t name, bool selected) {
    if (selected) arduboy.fillRect(x, y, WIDTH / MENU_COLS - 1, LINE_H - 1, WHITE);
    arduboy.setTextColor(selected ? BLACK : WHITE);
    arduboy.setTextBackground(selected ? WHITE : BLACK);
    arduboy.setCursor(x + 1, y);
    printFx(name, MENU_NAME_CHARS);
    arduboy.setTextColor(WHITE);
    arduboy.setTextBackground(BLACK);
  }

  void drawMenu() {
    arduboy.fillRect(0, 0, WIDTH, HEIGHT, BLACK);
    for (uint8_t v = 0; v < VERB_COUNT; ++v) {
      uint8_t r = v / MENU_COLS, c = v % MENU_COLS;
      drawMenuItem(c * (WIDTH / MENU_COLS), r * LINE_H, readVerb(v).name,
                   menuRow == r && menuCol == c);
    }
    arduboy.drawFastHLine(0, MENU_INV_TOP - 1, WIDTH, WHITE);
    if (!World::inventoryCount) {
      arduboy.setCursor(1, MENU_INV_TOP + 1);
      printFx(World::header.uiEmpty, MENU_NAME_CHARS * MENU_COLS);
      return;
    }
    for (uint8_t i = menuTop * MENU_COLS; i < World::inventoryCount; ++i) {
      uint8_t r = i / MENU_COLS, c = i % MENU_COLS;
      if (r >= menuTop + MENU_INV_ROWS) break;
      drawMenuItem(c * (WIDTH / MENU_COLS), MENU_INV_TOP + (r - menuTop) * MENU_INV_LINE_H,
                   objectName(World::inventory[i]),
                   menuRow == MENU_VERB_ROWS + r && menuCol == c);
    }
  }

  // Sprechtext: Kasten oben, horizontal über dem Sprecher, Zeilen zentriert.
  // top ist die Oberkante der ersten Zeile: 1 lässt über dem Text eine Pixel-
  // zeile Rand; ohne Rand (0) schließt die Leerzeile unter jedem Zeichen den
  // Kasten nach unten ab.
  void drawText(uint8_t top) {
    uint8_t lines = 1, maxLen = 0, len = 0;
    for (const char* p = text;; ++p) {
      if (*p == '\n' || !*p) {
        if (len > maxLen) maxLen = len;
        len = 0;
        if (!*p) break;
        ++lines;
      } else {
        ++len;
      }
    }
    // 1 px Rand links; rechts reicht die leere Abstandsspalte des Fonts plus 1 px.
    int16_t w = maxLen * CHAR_W + 2;
    int16_t x = (textActor == NONE8 ? WIDTH / 2 : World::screenX(textActor)) - w / 2;
    if (x < 0) x = 0;
    if (x > WIDTH - w) x = WIDTH - w;
    arduboy.fillRect(x, 0, w, top + lines * LINE_H, BLACK);

    const char* p = text;
    for (uint8_t row = 0; row < lines; ++row) {
      uint8_t n = 0;
      while (p[n] && p[n] != '\n') ++n;
      arduboy.setCursor(x + 1 + (maxLen - n) * CHAR_W / 2, top + row * LINE_H);
      for (uint8_t i = 0; i < n; ++i) arduboy.write(p[i]);
      p += n + (p[n] ? 1 : 0);
    }
  }

  // Gewählte Option: vollen Text laden; die Liste zeigt höchstens
  // CHOICE_ROWS Zeilen und weniger, wenn der volle Text sonst nicht darüber
  // passt (advc.py prüft: höchstens HEIGHT/LINE_H - 1 Zeilen). Die Liste
  // scrollt so, dass die Auswahl sichtbar bleibt.
  void showChoice() {
    World::readString(choiceText[choiceSel], text, sizeof(text));
    textActor = NONE8;
    choiceShown = choiceSel;
    uint8_t lines = 0;
    if (strchr(text, '\n'))
      for (const char* p = text; p; p = strchr(p + 1, '\n')) ++lines;
    uint8_t rows = HEIGHT / LINE_H - lines;
    if (rows > CHOICE_ROWS) rows = CHOICE_ROWS;
    if (rows > choiceCount) rows = choiceCount;
    choiceRows = rows;
    if (choiceSel < choiceTop) choiceTop = choiceSel;
    if (choiceSel >= choiceTop + rows) choiceTop = choiceSel - rows + 1;
  }

  // Dialogoptionen: unten die Liste (erste Zeile je Option), oben der volle
  // Text der gewählten Option, falls er umbricht. Der Text steht bündig
  // oben, damit seine letzte Zeile nicht in die Trennlinie über der Liste
  // reicht. Weitere Optionen über oder unter dem sichtbaren Teil zeigt ein
  // Pfeil am rechten Rand.
  void drawChoices() {
    if (choiceShown != choiceSel) showChoice();
    if (strchr(text, '\n')) drawText(0);
    uint8_t rows = choiceRows;
    uint8_t top = HEIGHT - rows * LINE_H;
    arduboy.fillRect(0, top - 1, WIDTH, HEIGHT - top + 1, BLACK);
    arduboy.drawFastHLine(0, top - 1, WIDTH, WHITE);
    for (uint8_t r = 0; r < rows; ++r) {
      uint8_t i = choiceTop + r;
      arduboy.setCursor(0, top + r * LINE_H);
      arduboy.print(i == choiceSel ? '>' : ' ');
      printFx(choiceText[i], OPTION_COLS);
    }
    bool up = choiceTop, down = choiceTop + rows < choiceCount;
    if (rows == 1 && up && down) {
      arduboy.setCursor(WIDTH - CHAR_W, top);
      arduboy.print('\x12');  // ↕ (CP437)
      return;
    }
    arduboy.setCursor(WIDTH - CHAR_W, top);
    if (up) arduboy.print('\x18');  // ↑
    arduboy.setCursor(WIDTH - CHAR_W, HEIGHT - LINE_H);
    if (down) arduboy.print('\x19');  // ↓
  }
}

void Ui::reset() {
  cardImage = NONE24;
  flashFrames = 0;
  arduboy.invert(false);
  resetSentence();
  pending = false;
  menuOpen = false;
  textFrames = 0;
  choiceActive = false;
  cursorX = WIDTH / 2;
  cursorY = HEIGHT / 2;
}

void Ui::say(uint8_t actor, uint24_t address) {
  uint8_t len = World::readString(address, text, sizeof(text));
  textActor = actor;
  World::talker = actor;
  textFrames = 45 + len * 4;  // grob Lesegeschwindigkeit bei 60 fps
}

void Ui::flash(uint8_t frames) {
  flashFrames = frames;
}

uint8_t Ui::hovered() {
  return hover;
}

bool Ui::talking() {
  return textFrames != 0;
}

void Ui::card(uint24_t image, uint8_t music) {
  cardImage = image;
  cardMusic = music != NONE8;
  cardFrames = 180;
  if (cardMusic) Sound::play(music);
}

bool Ui::showingCard() {
  return cardImage != NONE24;
}

void Ui::drawCard() {
  FX::drawBitmap(0, 0, cardImage, 0, dbmNormal);
}

void Ui::beginChoice() {
  choiceCount = 0;
  choiceSel = 0;
  choiceTop = 0;
  choiceShown = NONE8;
  choiceActive = false;
}

void Ui::addChoice(uint24_t address, uint24_t target) {
  if (choiceCount >= MAX_OPTIONS) return;
  choiceText[choiceCount] = address;
  choiceTarget[choiceCount] = target;
  ++choiceCount;
}

bool Ui::ask() {
  choiceActive = choiceCount != 0;
  return choiceActive;
}

bool Ui::choosing() {
  return choiceActive;
}

void Ui::update() {
  if (flashFrames) {
    --flashFrames;
    arduboy.invert(flashFrames && (flashFrames & 4));
  }
  if (cardImage != NONE24) {
    bool over = cardMusic ? !Sound::playing() : --cardFrames == 0;
    if (over || arduboy.justPressed(A_BUTTON)) {
      if (cardMusic) Sound::stop();
      cardImage = NONE24;
    }
    return;
  }
  if (textFrames) {
    if (arduboy.justPressed(A_BUTTON) || --textFrames == 0) {
      textFrames = 0;
      World::talker = NONE8;
    }
    return;
  }
  if (choiceActive) {
    if (arduboy.justPressed(UP_BUTTON) && choiceSel > 0) --choiceSel;
    if (arduboy.justPressed(DOWN_BUTTON) && choiceSel + 1 < choiceCount) ++choiceSel;
    if (choiceSel != choiceShown) showChoice();
    if (arduboy.justPressed(A_BUTTON)) {
      choiceActive = false;
      Script::chosen(choiceTarget[choiceSel]);
    }
    return;
  }
  // Läuft ein Skript (etwa eine Szene, die ein Ablauf ausgelöst hat), wartet
  // ein angefangener Satz, bis es fertig ist.
  if (Script::running()) return;
  if (pending && !World::isWalking(PLAYER)) {
    pending = false;
    World::face(PLAYER, pendingFace);
    if (pendingScript != NONE24) {
      Script::start(pendingScript);
      return;
    }
  }

  if (menuOpen) {
    if (arduboy.justPressed(UP_BUTTON)) menuMove(-1, 0);
    if (arduboy.justPressed(DOWN_BUTTON)) menuMove(1, 0);
    if (arduboy.justPressed(LEFT_BUTTON)) menuMove(0, -1);
    if (arduboy.justPressed(RIGHT_BUTTON)) menuMove(0, 1);
    if (arduboy.justPressed(A_BUTTON)) menuSelect();
    else if (arduboy.justPressed(B_BUTTON)) menuOpen = false;
    return;
  }

  updateCursor();
  hover = cursorY >= BAR_H ? World::hitTest(cursorX + World::scrollX, cursorY + World::scrollY) : NONE8;

  if (arduboy.justPressed(B_BUTTON)) {
    menuOpen = true;
    menuRow = verb / MENU_COLS;
    menuCol = verb % MENU_COLS;
    menuTop = 0;
    return;
  }
  if (arduboy.justPressed(A_BUTTON)) {
    pending = false;
    if (hover != NONE8) {
      pick(hover);
    } else if (cursorY >= BAR_H) {
      // Leere Stelle: hinlaufen, ein halbfertiger Satz wird verworfen.
      resetSentence();
      World::walkTo(PLAYER, cursorX + World::scrollX, cursorY + World::scrollY);
    }
  }
}

void Ui::draw() {
  if (menuOpen) {
    drawMenu();
    return;
  }
  if (textFrames) drawText(1);
  if (choiceActive) drawChoices();
  if (!Script::running() && !textFrames && !choiceActive) {
    drawSentence();
    FX::drawBitmap(cursorX - 4, cursorY - 4, World::header.cursor, 0, dbmMasked);
  }
}
