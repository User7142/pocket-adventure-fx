#include "Ui.h"
#include "Sound.h"
#include "Script.h"
#include "World.h"

namespace {
  constexpr uint8_t CHAR_W = 6;
  constexpr uint8_t LINE_H = 8;
  constexpr uint8_t BAR_H = 8;           // sentence line at the top
  constexpr uint8_t MENU_COLS = 2;
  constexpr uint8_t MENU_NAME_CHARS = 10;
  constexpr uint8_t MENU_VERB_ROWS = (VERB_COUNT + MENU_COLS - 1) / MENU_COLS;
  // Below the verbs a row across the full width: greyscale on/off
  constexpr uint8_t MENU_GREY_ROW = MENU_VERB_ROWS;
  constexpr uint8_t MENU_INV_ROW = MENU_GREY_ROW + 1;  // first inventory row
  constexpr uint8_t MENU_INV_TOP = MENU_INV_ROW * LINE_H + 1;
  constexpr uint8_t MENU_INV_LINE_H = 7;
  // The display does not show the bottom pixel row (ArduboyG, park row).
  constexpr uint8_t MENU_INV_ROWS = (HEIGHT - 1 - MENU_INV_TOP) / MENU_INV_LINE_H;
  static_assert(MENU_INV_ROWS >= 1, "no room for the inventory in the menu");

  // --- Speech text ---
  char text[TEXT_BUFFER];  // speech bubble or full text of the selected option
  uint8_t textActor = NONE8;
  uint16_t textFrames;  // 0 = no text
  uint24_t cardImage = NONE24;
  uint24_t cardGrey;
  bool cardMusic;
  uint8_t cardFrames;

  // --- Dialogue choice ---
  uint24_t choiceText[MAX_OPTIONS];
  uint24_t choiceTarget[MAX_OPTIONS];
  uint8_t choiceCount;
  uint8_t choiceSel;
  uint8_t choiceTop;            // first visible option (list scrolls)
  uint8_t choiceShown = NONE8;  // option whose full text is in text[]
  uint8_t choiceRows;           // visible rows of the list (depending on the text length)
  uint8_t flashFrames;          // flash: remaining duration
  bool choiceActive;

  // --- Sentence ---
  uint8_t verb = 0;             // 0 = “Walk to” (enforced by the compiler)
  uint8_t firstObject = NONE8;  // set while the second object is missing
  uint8_t hover = NONE8;

  // Sentence executed as soon as the player character has arrived
  bool pending;
  uint8_t pendingFace;
  uint24_t pendingScript;

  // --- Cursor and menu ---
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

  // Script for “verb object [other]” from the object's verb table.
  // With any, “on other” also applies (VERB_ANY: all remaining verbs except
  // “Walk to”, like verb 255 in the original), but only after the exact ones.
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

  // Executes a complete sentence: look up a matching script (in both
  // directions, then the verb's default response), walking to the object first.
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

  // An object was clicked (in the room or in the inventory).
  void pick(uint8_t object) {
    if (firstObject != NONE8) {
      execute(verb, firstObject, object);
      return;
    }
    // Two-object verbs (“Use … with …”) wait for the second object
    // if there is no response for the first one alone.
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

  // --- Menu: rows 0..MENU_VERB_ROWS-1 verbs, then inventory ---

  uint8_t menuItems(uint8_t row) {
    if (row < MENU_VERB_ROWS) {
      uint8_t first = row * MENU_COLS;
      return VERB_COUNT - first < MENU_COLS ? VERB_COUNT - first : MENU_COLS;
    }
    if (row == MENU_GREY_ROW) return 1;
    uint8_t first = (row - MENU_INV_ROW) * MENU_COLS;
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
    // The inventory scrolls so that the selection stays visible.
    if (menuRow >= MENU_INV_ROW) {
      uint8_t invRow = menuRow - MENU_INV_ROW;
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
    if (menuRow == MENU_GREY_ROW) {
      Settings::setGreyscale(!greyscale);
      return;
    }
    uint8_t item = World::inventory[(menuRow - MENU_INV_ROW) * MENU_COLS + menuCol];
    if (verb == 0) {
      // “Walk to” on an inventory object: verb from the game description.
      if (World::header.inventoryVerb == NONE8) return;
      verb = World::header.inventoryVerb;
    }
    pick(item);
  }

  // --- Drawing ---

  // First line of a string from the FX flash, at most maxChars characters.
  void printFx(uint24_t address, uint8_t maxChars) {
    char buf[TEXT_COLS + 2];
    if (maxChars > sizeof(buf) - 1) maxChars = sizeof(buf) - 1;
    World::readString(address, buf, maxChars + 1);
    char* nl = strchr(buf, '\n');
    if (nl) *nl = 0;
    arduboy.print(buf);
  }

  // Appends “ word” from the FX flash to the sentence buffer.
  uint8_t appendFx(char* buf, uint8_t len, uint8_t size, uint24_t address) {
    if (len && len < size - 1) buf[len++] = ' ';
    return len + World::readString(address, buf + len, size - len);
  }

  // Sentence line: “Verb Object [prep Object]”. If the sentence does not fit on
  // one line, its end is shown – that is where the object under the cursor is.
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

  void drawMenuItem(uint8_t x, uint8_t y, uint24_t name, bool selected, uint8_t cols = 1) {
    if (selected) arduboy.fillRect(x, y, cols * (WIDTH / MENU_COLS) - 1, LINE_H - 1, WHITE);
    arduboy.setTextColor(selected ? BLACK : WHITE);
    arduboy.setTextBackground(selected ? WHITE : BLACK);
    arduboy.setCursor(x + 1, y);
    printFx(name, cols * MENU_NAME_CHARS + (cols - 1));
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
    drawMenuItem(0, MENU_GREY_ROW * LINE_H, greyscale ? World::header.uiGreyOn : World::header.uiGreyOff,
                 menuRow == MENU_GREY_ROW, MENU_COLS);
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
                   menuRow == MENU_INV_ROW + r && menuCol == c);
    }
  }

  // Speech text: box at the top, horizontally above the speaker, lines centred.
  // top is the upper edge of the first line: 1 leaves a pixel row of margin
  // above the text; without margin (0) the empty row below each character
  // closes off the box at the bottom.
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
    // 1 px margin on the left; on the right the font's empty spacing column plus 1 px suffices.
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
      arduboy.write(reinterpret_cast<const uint8_t*>(p), n);
      p += n + (p[n] ? 1 : 0);
    }
  }

  // Selected option: load the full text; the list shows at most
  // CHOICE_ROWS rows, and fewer if the full text would not fit above it
  // otherwise (advc.py checks: at most HEIGHT/LINE_H - 1 lines). The list
  // scrolls so that the selection stays visible.
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

  // Dialogue options: the list at the bottom (first line per option), the full
  // text of the selected option at the top, if it wraps. The text is flush
  // with the top so that its last line does not reach into the separator line
  // above the list. More options above or below the visible part are shown
  // by an arrow at the right edge.
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
  textFrames = 45 + len * 4;  // rough reading speed at 60 fps
}

void Ui::flash(uint8_t frames) {
  flashFrames = frames;
}

bool Ui::inverted() {
  return flashFrames & 4;
}

uint8_t Ui::hovered() {
  return hover;
}

bool Ui::talking() {
  return textFrames != 0;
}

void Ui::card(uint24_t image, uint24_t grey, uint8_t music) {
  cardImage = image;
  cardGrey = grey;
  cardMusic = music != NONE8;
  cardFrames = 180;
  if (cardMusic) Sound::play(music);
}

bool Ui::showingCard() {
  return cardImage != NONE24;
}

void Ui::drawCard() {
  drawScreen(0, 0, cardImage, cardGrey);
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
  if (flashFrames) --flashFrames;
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
  // If a script is running (e.g. a scene triggered by a background routine), a
  // started sentence waits until it has finished.
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
      // Empty spot: walk there; a half-finished sentence is discarded.
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
