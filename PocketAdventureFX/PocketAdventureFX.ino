// Pocket Adventure FX – Adventure-Engine für den Arduboy FX.
//
// Die Engine ist datengetrieben: Räume, Grafiken, Texte, Skripte und Musik
// stehen im FX-Flash (game/game.adv → tools/advc.py → fxdata/game.bin), je
// Sprache ein Satz Texte und Skripte. Im Sketch selbst steckt nur der
// Interpreter. Details: README.md.

#include <EEPROM.h>

#include "Common.h"
#include "fxdata/fxdata.h"
#include "Script.h"
#include "Sound.h"
#include "Ui.h"
#include "World.h"

// advc.py legt alle Adressen relativ zum Anfang von game.bin an.
static_assert(gameData == 0, "game.bin muss die erste Ressource in fxdata.txt sein");

Arduboy2 arduboy;

namespace {
  enum class Mode : uint8_t { Language, Title, Play, BadData };
  Mode mode;

  // Gewählte Sprache im EEPROM: Kennbyte und Index (Platz für Spiele ab
  // EEPROM_STORAGE_SPACE_START, Lage beliebig gewählt).
  constexpr uint16_t EEPROM_LANG = EEPROM_STORAGE_SPACE_START + 0x1F0;
  constexpr uint8_t EEPROM_LANG_SIGNATURE = 0x4D;  // 'M'
  uint8_t language;

  uint8_t savedLanguage() {
    if (EEPROM.read(EEPROM_LANG) != EEPROM_LANG_SIGNATURE) return 0;
    uint8_t i = EEPROM.read(EEPROM_LANG + 1);
    return i < World::languageCount() ? i : 0;
  }

  void saveLanguage(uint8_t i) {
    EEPROM.update(EEPROM_LANG, EEPROM_LANG_SIGNATURE);
    EEPROM.update(EEPROM_LANG + 1, i);
  }

  void drawCentered(const __FlashStringHelper* s, uint8_t len, int16_t y) {
    arduboy.setCursor((WIDTH - len * 6) / 2, y);
    arduboy.print(s);
  }

  // Text aus dem FX-Flash, waagerecht zentriert.
  void drawCenteredFx(uint24_t address, int16_t y) {
    char buf[22];
    uint8_t len = World::readString(address, buf, sizeof(buf));
    arduboy.setCursor((WIDTH - len * 6) / 2, y);
    arduboy.print(buf);
  }

  // Sprachauswahl über dem Titelbild: Namen der enthaltenen Sprachen.
  void languageSelect() {
    uint8_t count = World::languageCount();
    if (arduboy.justPressed(UP_BUTTON) && language > 0) --language;
    if (arduboy.justPressed(DOWN_BUTTON) && language + 1 < count) ++language;
    if (arduboy.justPressed(A_BUTTON)) {
      saveLanguage(language);
      World::selectLanguage(language);
      mode = Mode::Title;
      return;
    }
    FX::drawBitmap(0, 0, World::header.title, 0, dbmNormal);
    constexpr uint8_t LINE_H = 9;
    int16_t top = (HEIGHT - count * LINE_H) / 2;
    arduboy.fillRect(30, top - 2, WIDTH - 60, count * LINE_H + 3, BLACK);
    arduboy.drawRect(30, top - 2, WIDTH - 60, count * LINE_H + 3, WHITE);
    for (uint8_t i = 0; i < count; ++i) {
      int16_t y = top + i * LINE_H;
      if (i == language) {
        arduboy.fillRect(32, y - 1, WIDTH - 64, LINE_H - 1, WHITE);
        arduboy.setTextColor(BLACK);
        arduboy.setTextBackground(WHITE);
      }
      drawCenteredFx(World::languageName(i), y);
      arduboy.setTextColor(WHITE);
      arduboy.setTextBackground(BLACK);
    }
  }

  void title() {
    if (arduboy.justPressed(B_BUTTON)) {
      arduboy.audio.toggle();
      arduboy.audio.saveOnOff();
    }
    if (arduboy.justPressed(A_BUTTON)) {
      World::reset();
      Ui::reset();
      Script::stopRoutine();
      Script::start(World::header.startScript);
      mode = Mode::Play;
      return;
    }
    // Das Titelbild bringt das Logo mit; darunter nur eine Hinweisleiste.
    FX::drawBitmap(0, 0, World::header.title, 0, dbmNormal);
    arduboy.fillRect(0, HEIGHT - 8, WIDTH, 8, BLACK);
    drawCenteredFx(arduboy.audio.enabled() ? World::header.uiSoundOn : World::header.uiSoundOff, HEIGHT - 7);
  }

  void play() {
    Script::update();
    World::update();
    Ui::update();
    if (Ui::showingCard()) {
      Ui::drawCard();
      return;
    }
    World::draw();
    Ui::draw();
  }

  // Ohne passende Daten gibt es keine Sprache: zweisprachiger Hinweis.
  void badData() {
    drawCentered(F("FX data missing"), 15, 4);
    drawCentered(F("or out of date."), 15, 13);
    drawCentered(F("FX-Daten fehlen"), 15, 28);
    drawCentered(F("oder sind veraltet."), 19, 37);
    drawCentered(F("./build.sh"), 10, 52);
  }
}

void setup() {
  arduboy.begin();
  arduboy.setFrameRate(60);
  FX::begin(FX_DATA_PAGE);
  if (!World::begin()) {
    mode = Mode::BadData;
    return;
  }
  language = savedLanguage();
  World::selectLanguage(language);
  Sound::begin(World::header.music, World::header.musicCount);
  Sound::play(World::header.titleMusic);
  mode = World::languageCount() > 1 ? Mode::Language : Mode::Title;
}

void loop() {
  if (!arduboy.nextFrame()) return;
  arduboy.pollButtons();
  switch (mode) {
    case Mode::Language:
      languageSelect();
      break;
    case Mode::Title:
      title();
      break;
    case Mode::Play:
      play();
      break;
    case Mode::BadData:
      badData();
      break;
  }
  // Nachfüllen erst nach allen Zeichenaufrufen: kein FX-Lesevorgang offen.
  if (mode != Mode::BadData) Sound::update();
  FX::display(CLEAR_BUFFER);
}
