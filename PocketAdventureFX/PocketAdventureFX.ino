// Pocket Adventure FX – adventure engine for the Arduboy FX.
//
// The engine is data-driven: rooms, graphics, texts, scripts and music
// live in the FX flash (game/game.adv → tools/advc.py → fxdata/game.bin), one
// set of texts and scripts per language. The sketch itself only contains the
// interpreter. Details: README.md.
//
// Frame composition (ArduboyG, Common.h): the display shows three planes in turn.
// loop() runs once per plane: wait until the previous plane has been shown,
// advance the game logic if needed (60 times per second, as previously
// with nextFrame), then draw the next plane.

#include <EEPROM.h>

#define ABG_IMPLEMENTATION
#include "Common.h"
#include "fxdata/fxdata.h"
#include "Script.h"
#include "Sound.h"
#include "Ui.h"
#include "World.h"

// advc.py lays out all addresses relative to the start of game.bin.
static_assert(gameData == 0, "game.bin must be the first resource in fxdata.txt");

Arduboy arduboy;
bool greyscale = true;

namespace {
  // Logic steps per second: walking speed, text duration, wait etc. are given in
  // these steps.
  constexpr uint8_t UPDATES_PER_SECOND = 60;

  enum class Mode : uint8_t { Language, Title, Play, BadData };
  Mode mode;

  // Settings in EEPROM: signature byte, language, greyscale (space for games
  // from EEPROM_STORAGE_SPACE_START, location chosen arbitrarily). Older versions
  // without the greyscale byte have 0xFF there: then the default (on) applies.
  constexpr uint16_t EEPROM_LANG = EEPROM_STORAGE_SPACE_START + 0x1F0;
  constexpr uint16_t EEPROM_GREY = EEPROM_LANG + 2;
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

  bool savedGreyscale() {
    return EEPROM.read(EEPROM_LANG) != EEPROM_LANG_SIGNATURE || EEPROM.read(EEPROM_GREY) != 0;
  }

  void drawCentered(const __FlashStringHelper* s, uint8_t len, int16_t y) {
    arduboy.setCursor((WIDTH - len * 6) / 2, y);
    arduboy.print(s);
  }

  // Text from the FX flash, centred horizontally.
  void drawCenteredFx(uint24_t address, int16_t y) {
    char buf[22];
    uint8_t len = World::readString(address, buf, sizeof(buf));
    arduboy.setCursor((WIDTH - len * 6) / 2, y);
    arduboy.print(buf);
  }

  void drawTitleImage() {
    drawScreen(0, 0, World::header.title, World::header.titleGrey);
  }

  // --- Language selection over the title image: names of the included languages ---

  void updateLanguage() {
    uint8_t count = World::languageCount();
    if (arduboy.justPressed(UP_BUTTON) && language > 0) --language;
    if (arduboy.justPressed(DOWN_BUTTON) && language + 1 < count) ++language;
    if (arduboy.justPressed(A_BUTTON)) {
      saveLanguage(language);
      World::selectLanguage(language);
      mode = Mode::Title;
    }
  }

  void drawLanguage() {
    uint8_t count = World::languageCount();
    drawTitleImage();
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

  // --- Title ---

  void updateTitle() {
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
    }
  }

  // The title image brings the logo; below it only a hint bar.
  void drawTitle() {
    drawTitleImage();
    arduboy.fillRect(0, HEIGHT - 8, WIDTH, 8, BLACK);
    drawCenteredFx(arduboy.audio.enabled() ? World::header.uiSoundOn : World::header.uiSoundOff, HEIGHT - 8);
  }

  // --- Game ---

  void updatePlay() {
    Script::update();
    World::update();
    Ui::update();
    World::prepare();
  }

  void drawPlay() {
    if (Ui::showingCard()) {
      Ui::drawCard();
      return;
    }
    World::draw();
    Ui::draw();
  }

  // Without matching data there is no language: bilingual notice.
  void drawBadData() {
    drawCentered(F("FX data missing"), 15, 4);
    drawCentered(F("or out of date."), 15, 13);
    drawCentered(F("FX-Daten fehlen"), 15, 28);
    drawCentered(F("oder sind veraltet."), 19, 37);
    drawCentered(F("./build.sh"), 10, 52);
  }

  // One logic step.
  void update() {
    ++arduboy.frameCount;  // animation cycle of the engine (Arduboy2 otherwise counts in nextFrame)
    arduboy.pollButtons();
    switch (mode) {
      case Mode::Language:
        updateLanguage();
        break;
      case Mode::Title:
        updateTitle();
        break;
      case Mode::Play:
        updatePlay();
        break;
      case Mode::BadData:
        return;
    }
    // Refill only after all reads: no FX access open.
    Sound::update();
  }

  // Draw one plane; greyscale images pick their frame by plane.
  void render() {
    switch (mode) {
      case Mode::Language:
        drawLanguage();
        break;
      case Mode::Title:
        drawTitle();
        break;
      case Mode::Play:
        drawPlay();
        break;
      case Mode::BadData:
        drawBadData();
        break;
    }
  }

  // Display and FX flash share the SPI bus; FX::begin() deselects the
  // display, and FX accesses leave it deselected. ArduboyG addresses the
  // display directly – so select it only for the duration of the transfer,
  // as FX::display() does. The same applies to commands such as invert().
  bool shownInverted;

  void showPlane() {
    FX::enableOLED();
    arduboy.waitForNextPlane();
    bool inverted = mode == Mode::Play && Ui::inverted();
    if (inverted != shownInverted) {
      arduboy.invert(inverted);
      shownInverted = inverted;
    }
    FX::disableOLED();
  }
}

void Settings::setGreyscale(bool on) {
  greyscale = on;
  EEPROM.update(EEPROM_LANG, EEPROM_LANG_SIGNATURE);
  EEPROM.update(EEPROM_GREY, on);
}

void setup() {
  arduboy.begin();
  // Arduboy2 starts with its white (1); for ArduboyG 1 is dark grey –
  // text without its own colour (the speech bubbles) would only be in plane 0.
  arduboy.setTextColor(WHITE);
  arduboy.setTextBackground(BLACK);
  arduboy.setUpdateHz(UPDATES_PER_SECOND);
  FX::begin(FX_DATA_PAGE);
  if (!World::begin()) {
    mode = Mode::BadData;
  } else {
    language = savedLanguage();
    greyscale = savedGreyscale();
    World::selectLanguage(language);
    Sound::begin(World::header.music, World::header.musicCount);
    Sound::play(World::header.titleMusic);
    mode = World::languageCount() > 1 ? Mode::Language : Mode::Title;
  }
  FX::enableOLED();
  arduboy.startGray();
  FX::disableOLED();
}

#ifdef RENDER_TIMING
// Development (make TIMING=1): bottom right, the longest time for game logic
// and drawing of one plane in µs, refreshed every second. The time window of
// one plane is 1 s / 156 ≈ 6400 µs minus the transfer to the display.
namespace {
  uint16_t worstUs, shownUs, timingUpdates;

  void drawTiming() {
    char buf[6];
    uint8_t len = snprintf(buf, sizeof(buf), "%u", shownUs);
    arduboy.fillRect(WIDTH - len * 6 - 1, HEIGHT - 9, len * 6 + 1, 9, BLACK);
    arduboy.setCursor(WIDTH - len * 6, HEIGHT - 8);
    arduboy.print(buf);
  }
}
#endif

void loop() {
  showPlane();
#ifdef RENDER_TIMING
  uint32_t start = micros();
  bool updated = arduboy.needsUpdate();
  if (updated) update();
  render();
  drawTiming();
  uint16_t us = micros() - start;
  if (us > worstUs) worstUs = us;
  if (updated && ++timingUpdates == UPDATES_PER_SECOND) {
    shownUs = worstUs;
    worstUs = timingUpdates = 0;
  }
#else
  if (arduboy.needsUpdate()) update();
  render();
#endif
}
