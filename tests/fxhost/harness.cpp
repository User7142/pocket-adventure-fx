// Host-Prüfstand für die Engine: dieselben Quellen wie auf dem Arduboy, gegen
// eine Nachbildung von Arduboy2/ArduboyFX übersetzt, mit game.bin als FX-Flash.
//
// Der Prüfstand spielt wie ein Mensch – er drückt Tasten, fährt den Cursor
// auf ein Objekt und wählt Verben im Menü – und schreibt mit, was passiert:
// Raumwechsel, Texte, Dialogoptionen, Karten, Musik, Türzustände, Figuren.
// tests/test_fxhost.py vergleicht diese Spur mit den Texten der eigenen
// Originalkopie, das Repo selbst enthält also keine Originaltexte.
//
// Aufruf: fxhost <game.bin> < befehle
//   lang <n>          Sprache n in der Auswahl wählen
//   start             Titelbild: A
//   idle              warten, bis kein Skript, Text, Dialog oder Karte mehr läuft
//   frames <n>        n Frames ohne Eingabe
//   choose <n>        im laufenden Dialog Option n (ab 0) wählen
//   select <n>        Auswahl auf Option n bewegen, ohne zu bestätigen
//   do <verb> <obj>   Verb im Menü wählen, Cursor aufs Objekt, A
//   verb <verb>       nur das Verb im Menü wählen
//   inv <verb> <obj>  Verb und dann einen Gegenstand im Inventar wählen
//   aim <obj>         nur den Cursor aufs Objekt fahren (läuft nicht hin)
//   click             A drücken
//   press <taste>     eine Taste antippen: a b up down left right
//   walk <x> <y>      in Raumkoordinaten hinlaufen (Klick auf leere Stelle)
//   seed <n>          Zufallsgenerator setzen
//   pos <actor>       Position eines Actors ausgeben
//   shot <datei.png>  aktuelles Bild speichern (4-fach vergrößert)
//   until <actor> <|> <x>   warten, bis der Actor im Raum ist und x (px) passt
//
// Ui.cpp wird hier eingebunden statt getrennt übersetzt: so sieht der
// Prüfstand Cursor, Menü und Dialogzustand, ohne dass die Engine dafür
// eine Testschnittstelle braucht.

#include "../../PocketAdventureFX/Ui.cpp"

#include <zlib.h>

namespace host {
  uint8_t buttons;
  std::vector<uint8_t> flash;
  uint8_t screen[WIDTH * HEIGHT / 8];
  uint8_t shown[WIDTH * HEIGHT / 8];  // zuletzt angezeigtes Bild
  bool inverted;

  void displayed(bool clear) {
    std::memcpy(shown, screen, sizeof(shown));
    if (clear) std::memset(screen, 0, sizeof(screen));
  }
  bool cursorDrawn;
  int16_t drawnCursorX, drawnCursorY;

  void drawn(int16_t x, int16_t y, uint32_t image, uint8_t) {
    if (image == uint32_t(World::header.cursor)) {
      cursorDrawn = true;
      drawnCursorX = x + 4;
      drawnCursorY = y + 4;
    }
  }
}

long random(long howbig) {
  return howbig > 0 ? std::rand() % howbig : 0;
}

// Ohne Lautsprecher: ein Stück „spielt“ eine feste Zeit, damit Karten mit
// Musik wie auf dem Gerät erst danach enden.
namespace Sound {
  constexpr uint16_t TRACK_FRAMES = 600;
  uint8_t track = NONE8;
  uint16_t left;
  uint8_t started = NONE8;  // in diesem Frame gestartet: meldet report() nach dem Raum

  void begin(uint24_t, uint8_t) {}
  void stop() {
    track = NONE8;
    left = 0;
  }
  void play(uint8_t t) {
    if (t == track && left) return;
    if (t != NONE8) started = t;
    track = t;
    left = t == NONE8 ? 0 : TRACK_FRAMES;
  }
  void update() {
    if (left && !--left) track = NONE8;
  }
  bool playing() { return left != 0; }
}

void setup();
void loop();

namespace {
  constexpr uint32_t LIMIT = 60 * 60 * 5;  // kein Befehl wartet länger als 5 Minuten Spielzeit
  uint32_t frame;

  // Was zuletzt berichtet wurde
  uint8_t lastRoom = NONE8;
  std::string lastText;
  bool lastChoice, lastCard;
  uint8_t lastState[OBJECT_COUNT];
  uint8_t lastActorRoom[ACTOR_COUNT];

  std::string fx(uint24_t address) {
    char buf[200];
    World::readString(address, buf, sizeof(buf));
    std::string s(buf);
    for (char& c : s)
      if (c == '\n') c = ' ';
    return s;
  }

  void report() {
    if (World::room != lastRoom) {
      lastRoom = World::room;
      std::printf("room %u\n", World::room);
    }
    if (Sound::started != NONE8) {
      std::printf("music %u\n", Sound::started);
      Sound::started = NONE8;
    }
    bool card = cardImage != NONE24;
    if (card && !lastCard) std::printf("card\n");
    lastCard = card;
    std::string t = textFrames ? std::string(text) : std::string();
    if (!t.empty() && t != lastText) {
      for (char& c : t)
        if (c == '\n') c = ' ';
      if (textActor == NONE8)
        std::printf("say - %s\n", t.c_str());
      else
        std::printf("say %u %s\n", textActor, t.c_str());
    }
    lastText = textFrames ? std::string(text) : std::string();
    if (choiceActive && !lastChoice) {
      for (uint8_t i = 0; i < choiceCount; ++i) std::printf("option %u %s\n", i, fx(choiceText[i]).c_str());
    }
    lastChoice = choiceActive;
    for (uint8_t o = 0; o < OBJECT_COUNT; ++o) {
      uint8_t s = World::objects[o] & OBJ_STATE;
      if (s != lastState[o]) std::printf("state %u %u\n", o, s);
      lastState[o] = s;
    }
    for (uint8_t a = 1; a < ACTOR_COUNT; ++a) {
      uint8_t r = World::actors[a].room;
      if (r != lastActorRoom[a]) {
        if (r == NONE8)
          std::printf("gone %u\n", a);
        else
          std::printf("here %u %u %u\n", a, World::actors[a].x >> SUBPIXEL_SHIFT, World::actors[a].y >> SUBPIXEL_SHIFT);
      }
      lastActorRoom[a] = r;
    }
    std::fflush(stdout);
  }

  void step(uint8_t pressed = 0) {
    host::buttons = pressed;
    host::cursorDrawn = false;
    loop();
    ++frame;
    report();
  }

  void tap(uint8_t button) {
    step(button);
    step();
  }

  [[noreturn]] void fail(const std::string& why) {
    std::printf("FEHLER %s (Frame %u)\n", why.c_str(), frame);
    std::exit(1);
  }

  // Bild als PNG: Graustufen, 4-fach, damit es sich ansehen lässt.
  void png(const std::string& path) {
    constexpr int S = 4, W = WIDTH * S, H = HEIGHT * S;
    std::vector<uint8_t> raw;
    for (int y = 0; y < H; ++y) {
      raw.push_back(0);  // Filter: keiner
      for (int x = 0; x < W; ++x) {
        int px = x / S, py = y / S;
        bool on = (host::shown[(py / 8) * WIDTH + px] >> (py & 7)) & 1;
        raw.push_back(on != host::inverted ? 0xFF : 0x00);
      }
    }
    uLongf len = compressBound(raw.size());
    std::vector<uint8_t> z(len);
    compress2(z.data(), &len, raw.data(), raw.size(), 9);
    z.resize(len);
    std::ofstream f(path, std::ios::binary);
    auto u32 = [&](uint32_t v) {
      uint8_t b[4] = {uint8_t(v >> 24), uint8_t(v >> 16), uint8_t(v >> 8), uint8_t(v)};
      f.write(reinterpret_cast<char*>(b), 4);
    };
    auto chunk = [&](const char* type, const std::vector<uint8_t>& data) {
      u32(data.size());
      uLong crc = crc32(0, reinterpret_cast<const Bytef*>(type), 4);
      crc = crc32(crc, data.data(), data.size());
      f.write(type, 4);
      f.write(reinterpret_cast<const char*>(data.data()), data.size());
      u32(crc);
    };
    f.write("\x89PNG\r\n\x1a\n", 8);
    std::vector<uint8_t> ihdr = {uint8_t(W >> 24), uint8_t(W >> 16), uint8_t(W >> 8), uint8_t(W),
                                 uint8_t(H >> 24), uint8_t(H >> 16), uint8_t(H >> 8), uint8_t(H), 8, 0, 0, 0, 0};
    chunk("IHDR", ihdr);
    chunk("IDAT", z);
    chunk("IEND", {});
  }

  bool busy() {
    return Script::running() || textFrames || choiceActive || cardImage != NONE24 || pending;
  }

  void idle() {
    for (uint32_t n = 0; busy(); ++n) {
      if (n > LIMIT) fail("idle: läuft nicht aus");
      step();
    }
  }

  // Cursor mit dem Steuerkreuz auf eine Bildschirmposition fahren.
  void moveCursor(int16_t x, int16_t y) {
    for (uint32_t n = 0; cursorX != x || cursorY != y; ++n) {
      if (n > 2000) fail("Cursor erreicht das Ziel nicht");
      int16_t dx = x - cursorX, dy = y - cursorY;
      // Ab 20 Frames Halten springt der Cursor um 2; kurz vor dem Ziel loslassen.
      bool near = (dx && std::abs(dx) < 3) || (dy && std::abs(dy) < 3);
      uint8_t b = 0;
      if (dx < 0) b |= LEFT_BUTTON;
      if (dx > 0) b |= RIGHT_BUTTON;
      if (dy < 0) b |= UP_BUTTON;
      if (dy > 0) b |= DOWN_BUTTON;
      if (near && holdFrames > 20) b = 0;
      step(b);
    }
  }

  void chooseVerb(uint8_t v) {
    idle();
    tap(B_BUTTON);
    if (!menuOpen) fail("Menü geht nicht auf");
    for (uint8_t n = 0; menuRow != v / MENU_COLS || menuCol != v % MENU_COLS; ++n) {
      if (n > 40) fail("Verb im Menü nicht erreichbar");
      if (menuRow > v / MENU_COLS) tap(UP_BUTTON);
      else if (menuRow < v / MENU_COLS) tap(DOWN_BUTTON);
      else if (menuCol > v % MENU_COLS) tap(LEFT_BUTTON);
      else tap(RIGHT_BUTTON);
    }
    tap(A_BUTTON);
  }

  // Gegenstand im Inventar über das Menü wählen (Verb vorher gewählt).
  void pickInventory(uint8_t object) {
    uint8_t i = 0;
    while (i < World::inventoryCount && World::inventory[i] != object) ++i;
    if (i == World::inventoryCount) fail("Gegenstand " + std::to_string(object) + " nicht im Inventar");
    tap(B_BUTTON);
    if (!menuOpen) fail("Menü geht nicht auf");
    uint8_t row = MENU_VERB_ROWS + i / MENU_COLS, col = i % MENU_COLS;
    for (uint8_t n = 0; menuRow != row || menuCol != col; ++n) {
      if (n > 60) fail("Gegenstand im Menü nicht erreichbar");
      if (menuRow < row) tap(DOWN_BUTTON);
      else if (menuRow > row) tap(UP_BUTTON);
      else if (menuCol < col) tap(RIGHT_BUTTON);
      else tap(LEFT_BUTTON);
    }
    tap(A_BUTTON);
  }

  // Ein sichtbarer Punkt, an dem hitTest das Objekt liefert.
  bool visiblePoint(uint8_t object, int16_t& sx, int16_t& sy) {
    PlaceRec p;
    if (!World::findPlace(object, p)) return false;
    for (int16_t y = p.y + p.h / 2, dy = 0; dy <= p.h; ++dy, y = p.y + p.h / 2 + ((dy & 1) ? -(dy + 1) / 2 : dy / 2)) {
      int16_t t = y - World::scrollY;
      if (t < BAR_H || t >= HEIGHT) continue;
      for (int16_t x = p.x + p.w / 2, dx = 0; dx <= p.w; ++dx, x = p.x + p.w / 2 + ((dx & 1) ? -(dx + 1) / 2 : dx / 2)) {
        int16_t s = x - World::scrollX;
        if (s < 0 || s >= WIDTH) continue;
        if (World::hitTest(x, y) == object) {
          sx = s;
          sy = t;
          return true;
        }
      }
    }
    return false;
  }

  void click(int16_t sx, int16_t sy) {
    moveCursor(sx, sy);
    tap(A_BUTTON);
  }

  void walk(int16_t x, int16_t y) {
    idle();
    // Ziel außerhalb des Bildes: in Etappen hinlaufen, die Kamera folgt.
    for (int n = 0; n < 12; ++n) {
      int16_t s = x - World::scrollX, t = y - World::scrollY;
      int16_t cs = s < 0 ? 0 : s >= WIDTH ? WIDTH - 1 : s;
      int16_t cy = t < BAR_H ? BAR_H : t >= HEIGHT ? HEIGHT - 1 : t;
      // Nur auf freie Stellen klicken: ein Objekt dort würde ausgelöst.
      auto hit = [&](int16_t px, int16_t py) { return World::hitTest(px + World::scrollX, py + World::scrollY); };
      for (int16_t d = 1; hit(cs, cy) != NONE8 && d < HEIGHT; ++d) {
        int16_t u = (d & 1) ? cy + (d + 1) / 2 : cy - d / 2;
        if (u >= BAR_H && u < HEIGHT && hit(cs, u) == NONE8) cy = u;
      }
      for (int16_t d = 1; hit(cs, cy) != NONE8 && d < 40; ++d)
        cs = s < cs ? cs + 1 : cs - 1;
      click(cs, cy);
      for (uint32_t k = 0; busy() || World::isWalking(PLAYER); ++k) {
        if (k > LIMIT) fail("walk: läuft nicht aus");
        step();
      }
      if (cs == s && cy == t) return;
    }
  }

  void aim(uint8_t object) {
    int16_t sx, sy;
    if (!visiblePoint(object, sx, sy)) fail("Objekt " + std::to_string(object) + " nicht sichtbar");
    moveCursor(sx, sy);
  }

  void doVerb(uint8_t v, uint8_t object) {
    chooseVerb(v);
    int16_t sx, sy;
    for (int n = 0; !visiblePoint(object, sx, sy); ++n) {
      PlaceRec p;
      if (n > 5 || !World::findPlace(object, p)) fail("Objekt " + std::to_string(object) + " nicht im Raum");
      // Erst hinlaufen; dabei geht das gewählte Verb verloren, also neu wählen.
      if (p.walkX == WALK_DIRECT) walk(p.x + p.w / 2, p.y + p.h);
      else walk(p.walkX, p.walkY);
      chooseVerb(v);
    }
    click(sx, sy);
  }
}

int main(int argc, char** argv) {
  if (argc != 2) {
    std::fprintf(stderr, "Aufruf: fxhost <game.bin> < befehle\n");
    return 2;
  }
  std::ifstream in(argv[1], std::ios::binary);
  host::flash.assign(std::istreambuf_iterator<char>(in), {});
  if (host::flash.empty()) fail("game.bin fehlt");
  std::memset(lastActorRoom, NONE8, sizeof(lastActorRoom));
  std::srand(1);
  setup();

  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream ls(line);
    std::string cmd;
    if (!(ls >> cmd) || cmd[0] == '#') continue;
    std::printf("> %s\n", line.c_str());
    if (cmd == "lang") {
      int n;
      ls >> n;
      step();
      for (int i = 0; i < n; ++i) tap(DOWN_BUTTON);
      tap(A_BUTTON);
    } else if (cmd == "start") {
      tap(A_BUTTON);
    } else if (cmd == "idle") {
      idle();
    } else if (cmd == "frames") {
      int n;
      ls >> n;
      for (int i = 0; i < n; ++i) step();
    } else if (cmd == "choose" || cmd == "select") {
      int n;
      ls >> n;
      for (uint32_t k = 0; !choiceActive; ++k) {
        if (k > LIMIT) fail(cmd + ": kein Dialog");
        step();
      }
      if (n >= choiceCount) fail(cmd + ": nur " + std::to_string(choiceCount) + " Optionen");
      while (choiceSel > n) tap(UP_BUTTON);
      while (choiceSel < n) tap(DOWN_BUTTON);
      if (cmd == "choose") tap(A_BUTTON);
    } else if (cmd == "do") {
      int v, o;
      ls >> v >> o;
      doVerb(v, o);
    } else if (cmd == "verb") {
      int v;
      ls >> v;
      chooseVerb(v);
    } else if (cmd == "inv") {
      int v, o;
      ls >> v >> o;
      chooseVerb(v);
      pickInventory(o);
    } else if (cmd == "aim") {
      int o;
      ls >> o;
      aim(o);
    } else if (cmd == "click") {
      tap(A_BUTTON);
    } else if (cmd == "press") {
      std::string k;
      ls >> k;
      uint8_t b = k == "a" ? A_BUTTON : k == "b" ? B_BUTTON : k == "up" ? UP_BUTTON : k == "down" ? DOWN_BUTTON
                : k == "left" ? LEFT_BUTTON : k == "right" ? RIGHT_BUTTON : 0;
      if (!b) fail("press: unbekannte Taste " + k);
      tap(b);
    } else if (cmd == "walk") {
      int x, y;
      ls >> x >> y;
      walk(x, y);
    } else if (cmd == "seed") {
      unsigned s;
      ls >> s;
      std::srand(s);
    } else if (cmd == "until") {
      int a, x;
      std::string rel;
      ls >> a >> rel >> x;
      auto ok = [&] {
        int px = World::actors[a].x >> SUBPIXEL_SHIFT;
        return World::actors[a].room == World::room && (rel == "<" ? px < x : px > x);
      };
      for (uint32_t k = 0; !ok(); ++k) {
        if (k > LIMIT) fail("until: Bedingung tritt nicht ein");
        step();
      }
    } else if (cmd == "shot") {
      std::string path;
      ls >> path;
      png(path);
    } else if (cmd == "pos") {
      int a;
      ls >> a;
      std::printf("pos %d %u %u room %u scroll %d\n", a, World::actors[a].x >> SUBPIXEL_SHIFT,
                  World::actors[a].y >> SUBPIXEL_SHIFT, World::actors[a].room, World::scrollX);
    } else {
      fail("unbekannter Befehl " + cmd);
    }
  }
  std::printf("frames %u\n", frame);
  return 0;
}
