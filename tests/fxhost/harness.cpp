// Host test bench for the engine: the same sources as on the Arduboy, compiled
// against a stand-in for Arduboy2/ArduboyFX, with game.bin as the FX flash.
//
// The test bench plays like a human – it presses keys, moves the cursor
// onto an object and picks verbs in the menu – and records what happens:
// room changes, texts, dialogue options, cards, music, door states, characters.
// tests/test_fxhost.py compares this trace with the texts of your own
// copy of the game, so the repo itself contains no original texts.
//
// Run: fxhost <game.bin> < commands
//   lang <n>          pick language n in the selection
//   start             title screen: A
//   idle              wait until no script, text, dialogue or card is running
//   frames <n>        n frames without input
//   choose <n>        pick option n (from 0) in the running dialogue
//   select <n>        move the selection to option n without confirming
//   do <verb> <obj>   pick verb in the menu, cursor onto the object, A
//   verb <verb>       only pick the verb in the menu
//   inv <verb> <obj>  pick verb and then an item in the inventory
//   aim <obj>         only move the cursor onto the object (does not walk there)
//   click             press A
//   press <key>       tap a key: a b up down left right
//   walk <x> <y>      walk there in room coordinates (click on an empty spot)
//   seed <n>          set the random generator
//   pos <actor>       print an actor's position
//   shot <file.png>   save the current image (scaled up 4x)
//   grey              toggle greyscale on/off in the menu
//   drawcheck         check the engine's text and rectangles (Common.h) against the pixel reference
//   tones [x0 y0 x1 y1]  pixels per level in the displayed image (black … white),
//                     optionally only within the cutout
//   until <actor> <|> <x>   wait until the actor is in the room and x (px) matches
//
// Ui.cpp is included here instead of compiled separately: this way the
// test bench sees cursor, menu and dialogue state without the engine
// needing a test interface for it.

#include "../../PocketAdventureFX/Ui.cpp"

#include <zlib.h>

namespace host {
  uint8_t buttons;
  std::vector<uint8_t> flash;
  uint8_t screen[WIDTH * HEIGHT / 8];
  uint8_t shown[PLANES][WIDTH * HEIGHT / 8];  // last displayed planes (ArduboyG)
  bool inverted;

  // Like ArduboyG: transfer the plane, afterwards the buffer is empty.
  void displayed(uint8_t plane) {
    std::memcpy(shown[plane], screen, sizeof(screen));
    std::memset(screen, 0, sizeof(screen));
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

// Without a speaker: a track "plays" for a fixed time, so cards with
// music end only afterwards, as on the device.
namespace Sound {
  constexpr uint16_t TRACK_FRAMES = 600;
  uint8_t track = NONE8;
  uint16_t left;
  uint8_t started = NONE8;  // started in this frame: report() reports it after the room

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
  constexpr uint32_t LIMIT = 60 * 60 * 5;  // no command waits longer than 5 minutes of game time
  uint32_t frame;

  // What was last reported
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

  // One frame: draw every plane once, with one step of game logic
  // (shim/ArduboyG.h) – as on the device, just without its timing.
  void step(uint8_t pressed = 0) {
    host::buttons = pressed;
    host::cursorDrawn = false;
    for (uint8_t p = 0; p < host::PLANES; ++p) loop();
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

  // Image as PNG, 4x, so it can be viewed: per pixel the number of lit
  // planes as a grey value, the way the eye blends the three planes.
  void png(const std::string& path) {
    constexpr int S = 4, W = WIDTH * S, H = HEIGHT * S;
    std::vector<uint8_t> raw;
    for (int y = 0; y < H; ++y) {
      raw.push_back(0);  // filter: none
      for (int x = 0; x < W; ++x) {
        int px = x / S, py = y / S;
        uint8_t lit = 0;
        for (uint8_t p = 0; p < host::PLANES; ++p) lit += (host::shown[p][(py / 8) * WIDTH + px] >> (py & 7)) & 1;
        uint8_t grey = lit * 255 / host::PLANES;
        raw.push_back(host::inverted ? 255 - grey : grey);
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

  // Move the cursor to a screen position with the D-pad.
  void moveCursor(int16_t x, int16_t y) {
    for (uint32_t n = 0; cursorX != x || cursorY != y; ++n) {
      if (n > 2000) fail("Cursor erreicht das Ziel nicht");
      int16_t dx = x - cursorX, dy = y - cursorY;
      // After 20 frames of holding the cursor jumps by 2; release just before the target.
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

  // Pick the "greyscale on/off" line in the menu.
  void toggleGrey() {
    idle();
    tap(B_BUTTON);
    if (!menuOpen) fail("Menü geht nicht auf");
    for (uint8_t n = 0; menuRow != MENU_GREY_ROW; ++n) {
      if (n > 40) fail("Graustufen-Zeile im Menü nicht erreichbar");
      tap(menuRow < MENU_GREY_ROW ? DOWN_BUTTON : UP_BUTTON);
    }
    tap(A_BUTTON);
    std::printf("greyscale %d\n", greyscale);
  }

  // Arduboy::write and Arduboy::fillRect (Common.h) work page-wise on the
  // buffer; the result must match the reference pixel for pixel
  // (drawChar or fillRect of the stand-in, pixel by pixel) – for every
  // character, every position (also clipped), every colour and without
  // changing neighbouring pixels (buffer patterned beforehand).
  void drawCheck() {
    const uint8_t colors[][2] = {{WHITE, BLACK}, {BLACK, WHITE}, {WHITE, WHITE}, {BLACK, BLACK}};
    uint8_t fast[sizeof(host::screen)], reference[sizeof(host::screen)];
    uint32_t checked = 0;
    for (auto [fg, bg] : colors)
      for (int16_t y = -8; y <= HEIGHT; ++y)
        for (int16_t x : {-3, 61, 125})
          for (uint16_t c = 0; c < 256; ++c) {
            for (size_t i = 0; i < sizeof(host::screen); ++i) host::screen[i] = uint8_t(0xA5 ^ i);
            arduboy.setTextColor(fg);
            arduboy.setTextBackground(bg);
            arduboy.setCursor(x, y);
            arduboy.write(uint8_t(c == '\n' || c == '\r' ? ' ' : c));
            std::memcpy(fast, host::screen, sizeof(fast));
            for (size_t i = 0; i < sizeof(host::screen); ++i) host::screen[i] = uint8_t(0xA5 ^ i);
            Arduboy2::drawChar(x, y, uint8_t(c == '\n' || c == '\r' ? ' ' : c), fg, bg);
            std::memcpy(reference, host::screen, sizeof(reference));
            if (std::memcmp(fast, reference, sizeof(fast)))
              fail("textcheck: Zeichen " + std::to_string(c) + " bei " + std::to_string(x) + "," +
                   std::to_string(y) + " Farbe " + std::to_string(fg) + "/" + std::to_string(bg));
            ++checked;
          }
    // Whole texts (print → write(buffer, n)): line pieces, line breaks, edges
    const char* texts[] = {"Hello, I'm Guybrush!", "Two\nlines\r here", "\n\nend", "",
                           "A very long line that runs far past the right edge of the display"};
    for (auto [fg, bg] : colors)
      for (const char* t : texts)
        for (int16_t y : {-9, -3, 0, 1, 7, 33, 60})
          for (int16_t x : {-20, -1, 0, 5, 100}) {
            for (size_t i = 0; i < sizeof(host::screen); ++i) host::screen[i] = uint8_t(0xA5 ^ i);
            arduboy.setTextColor(fg);
            arduboy.setTextBackground(bg);
            arduboy.setCursor(x, y);
            arduboy.print(t);
            std::memcpy(fast, host::screen, sizeof(fast));
            for (size_t i = 0; i < sizeof(host::screen); ++i) host::screen[i] = uint8_t(0xA5 ^ i);
            arduboy.setCursor(x, y);
            for (const char* c = t; *c; ++c) arduboy.Arduboy2::write(uint8_t(*c));
            std::memcpy(reference, host::screen, sizeof(reference));
            if (std::memcmp(fast, reference, sizeof(fast)))
              fail(std::string("textcheck: Text \"") + t + "\" bei " + std::to_string(x) + "," + std::to_string(y));
            ++checked;
          }
    arduboy.setTextColor(WHITE);
    arduboy.setTextBackground(BLACK);
    std::printf("textcheck %u\n", checked);

    uint32_t rects = 0;
    for (uint8_t c : {WHITE, BLACK})
      for (int16_t y = -10; y <= HEIGHT; y += 1)
        for (uint8_t h : {0, 1, 3, 7, 8, 9, 17, 64, 255})
          for (int16_t x : {-5, 0, 37, 120})
            for (uint8_t w : {0, 1, 6, 64, 255}) {
              for (size_t i = 0; i < sizeof(host::screen); ++i) host::screen[i] = uint8_t(0x5A ^ i);
              Arduboy::fillRect(x, y, w, h, c);
              std::memcpy(fast, host::screen, sizeof(fast));
              for (size_t i = 0; i < sizeof(host::screen); ++i) host::screen[i] = uint8_t(0x5A ^ i);
              arduboy.Arduboy2::fillRect(x, y, w, h, c);
              std::memcpy(reference, host::screen, sizeof(reference));
              if (std::memcmp(fast, reference, sizeof(fast)))
                fail("drawcheck: fillRect(" + std::to_string(x) + ", " + std::to_string(y) + ", " +
                     std::to_string(w) + ", " + std::to_string(h) + ", " + std::to_string(c) + ")");
              ++rects;
            }
    std::printf("rectcheck %u\n", rects);

    // drawScreen reads aligned pages straight from the flash; every room at
    // several positions, 1 bit and every greyscale plane, like FX::drawBitmap.
    uint32_t screens = 0;
    bool wasGrey = greyscale;
    for (uint8_t r = 0; r < World::header.roomCount; ++r) {
      RoomRec rec;
      FX::readDataObject(World::header.rooms + uint24_t(r) * sizeof(RoomRec), rec);
      for (int16_t sx : {0, (rec.width - WIDTH) / 2, rec.width - WIDTH})
        for (int16_t sy = 0; sy <= rec.height - HEIGHT; sy += 8)
          for (uint8_t mode = 0; mode < 1 + host::PLANES; ++mode) {
            greyscale = mode > 0;
            uint8_t plane = mode > 0 ? mode - 1 : 0;
            while (arduboy.currentPlane() != plane) arduboy.waitForNextPlane();
            std::memset(host::screen, 0x5A, sizeof(host::screen));
            drawScreen(sx, sy, rec.background, rec.grey);
            std::memcpy(fast, host::screen, sizeof(fast));
            std::memset(host::screen, 0x5A, sizeof(host::screen));
            bool grey = greyscale && rec.grey != NONE24;
            FX::drawBitmap(-sx, -sy, grey ? rec.grey : rec.background, grey ? plane : 0, dbmNormal);
            std::memcpy(reference, host::screen, sizeof(reference));
            if (std::memcmp(fast, reference, sizeof(fast)))
              fail("drawcheck: drawScreen Raum " + std::to_string(r) + " bei " + std::to_string(sx) + "," +
                   std::to_string(sy) + " Modus " + std::to_string(mode));
            ++screens;
          }
    }
    greyscale = wasGrey;
    std::memset(host::screen, 0, sizeof(host::screen));
    std::printf("screencheck %u\n", screens);
  }

  // How many pixels of the displayed image are lit in how many planes –
  // 0 black, 1 dark grey, 2 light grey, 3 white.
  void tones(int16_t x0 = 0, int16_t y0 = 0, int16_t x1 = WIDTH, int16_t y1 = HEIGHT) {
    uint32_t count[host::PLANES + 1] = {};
    for (int16_t y = y0; y < y1; ++y)
      for (int16_t x = x0; x < x1; ++x) {
        uint8_t lit = 0;
        for (uint8_t p = 0; p < host::PLANES; ++p) lit += (host::shown[p][(y / 8) * WIDTH + x] >> (y & 7)) & 1;
        ++count[lit];
      }
    std::printf("tones %u %u %u %u\n", count[0], count[1], count[2], count[3]);
  }

  // Pick an item in the inventory via the menu (verb picked beforehand).
  void pickInventory(uint8_t object) {
    uint8_t i = 0;
    while (i < World::inventoryCount && World::inventory[i] != object) ++i;
    if (i == World::inventoryCount) fail("Gegenstand " + std::to_string(object) + " nicht im Inventar");
    tap(B_BUTTON);
    if (!menuOpen) fail("Menü geht nicht auf");
    uint8_t row = MENU_INV_ROW + i / MENU_COLS, col = i % MENU_COLS;
    for (uint8_t n = 0; menuRow != row || menuCol != col; ++n) {
      if (n > 60) fail("Gegenstand im Menü nicht erreichbar");
      if (menuRow < row) tap(DOWN_BUTTON);
      else if (menuRow > row) tap(UP_BUTTON);
      else if (menuCol < col) tap(RIGHT_BUTTON);
      else tap(LEFT_BUTTON);
    }
    tap(A_BUTTON);
  }

  // A visible point at which hitTest returns the object.
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
    // Target outside the screen: walk there in stages, the camera follows.
    for (int n = 0; n < 12; ++n) {
      int16_t s = x - World::scrollX, t = y - World::scrollY;
      int16_t cs = s < 0 ? 0 : s >= WIDTH ? WIDTH - 1 : s;
      int16_t cy = t < BAR_H ? BAR_H : t >= HEIGHT ? HEIGHT - 1 : t;
      // Only click on free spots: an object there would be triggered.
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
      // Walk there first; this loses the picked verb, so pick it again.
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
    } else if (cmd == "grey") {
      toggleGrey();
    } else if (cmd == "tones") {
      int x0, y0, x1, y1;
      if (ls >> x0 >> y0 >> x1 >> y1) tones(x0, y0, x1, y1);
      else tones();
    } else if (cmd == "drawcheck") {
      drawCheck();
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
