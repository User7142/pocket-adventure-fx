// Pocket Adventure FX – Adventure-Engine für den Arduboy FX.
//
// Die Engine ist datengetrieben: Räume, Grafiken, Texte, Skripte und Musik
// stehen im FX-Flash (game/game.adv → tools/advc.py → fxdata/game.bin), je
// Sprache ein Satz Texte und Skripte. Im Sketch selbst steckt nur der
// Interpreter. Details: README.md.
//
// Bildaufbau (ArduboyG, Common.h): Das Display zeigt drei Ebenen im Wechsel.
// loop() läuft einmal je Ebene: warten, bis die vorige Ebene gezeigt ist,
// bei Bedarf die Spiellogik weiterschalten (60-mal pro Sekunde, wie vorher
// mit nextFrame), dann die nächste Ebene zeichnen.

#include <EEPROM.h>

#define ABG_IMPLEMENTATION
#include "Common.h"
#include "fxdata/fxdata.h"
#include "Script.h"
#include "Sound.h"
#include "Ui.h"
#include "World.h"

// advc.py legt alle Adressen relativ zum Anfang von game.bin an.
static_assert(gameData == 0, "game.bin muss die erste Ressource in fxdata.txt sein");

Arduboy arduboy;
bool greyscale = true;

namespace {
  // Spiellogik je Sekunde: Laufgeschwindigkeit, Textdauer, wait usw. sind in
  // diesen Schritten angegeben.
  constexpr uint8_t UPDATES_PER_SECOND = 60;

  enum class Mode : uint8_t { Language, Title, Play, BadData };
  Mode mode;

  // Einstellungen im EEPROM: Kennbyte, Sprache, Graustufen (Platz für Spiele
  // ab EEPROM_STORAGE_SPACE_START, Lage beliebig gewählt). Ältere Stände
  // ohne Graustufen-Byte haben dort 0xFF: dann gilt die Vorgabe (an).
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

  // Text aus dem FX-Flash, waagerecht zentriert.
  void drawCenteredFx(uint24_t address, int16_t y) {
    char buf[22];
    uint8_t len = World::readString(address, buf, sizeof(buf));
    arduboy.setCursor((WIDTH - len * 6) / 2, y);
    arduboy.print(buf);
  }

  void drawTitleImage() {
    drawScreen(0, 0, World::header.title, World::header.titleGrey);
  }

  // --- Sprachauswahl über dem Titelbild: Namen der enthaltenen Sprachen ---

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

  // --- Titel ---

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

  // Das Titelbild bringt das Logo mit; darunter nur eine Hinweisleiste.
  void drawTitle() {
    drawTitleImage();
    arduboy.fillRect(0, HEIGHT - 8, WIDTH, 8, BLACK);
    drawCenteredFx(arduboy.audio.enabled() ? World::header.uiSoundOn : World::header.uiSoundOff, HEIGHT - 8);
  }

  // --- Spiel ---

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

  // Ohne passende Daten gibt es keine Sprache: zweisprachiger Hinweis.
  void drawBadData() {
    drawCentered(F("FX data missing"), 15, 4);
    drawCentered(F("or out of date."), 15, 13);
    drawCentered(F("FX-Daten fehlen"), 15, 28);
    drawCentered(F("oder sind veraltet."), 19, 37);
    drawCentered(F("./build.sh"), 10, 52);
  }

  // Ein Schritt Spiellogik.
  void update() {
    ++arduboy.frameCount;  // Animationstakt der Engine (Arduboy2 zählt sonst in nextFrame)
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
    // Nachfüllen erst nach allen Lesevorgängen: kein FX-Zugriff offen.
    Sound::update();
  }

  // Eine Ebene zeichnen; Graustufen-Bilder wählen ihren Frame nach der Ebene.
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

  // Display und FX-Flash teilen sich den SPI-Bus; FX::begin() wählt das
  // Display ab, und FX-Zugriffe lassen es abgewählt. ArduboyG spricht das
  // Display direkt an – also nur für die Dauer der Übertragung auswählen,
  // wie FX::display() es tut. Dasselbe gilt für Befehle wie invert().
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
  // Arduboy2 beginnt mit seinem Weiß (1); für ArduboyG ist 1 Dunkelgrau –
  // Text ohne eigene Farbe (die Sprechblasen) stünde nur in Ebene 0.
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
// Entwicklung (make TIMING=1): unten rechts die längste Zeit für Spiellogik
// und Zeichnen einer Ebene in µs, je Sekunde neu. Das Zeitfenster einer
// Ebene ist 1 s / 156 ≈ 6400 µs abzüglich der Übertragung ans Display.
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
