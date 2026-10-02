#include "Script.h"
#include "Sound.h"
#include "Ui.h"
#include "World.h"

namespace {
  enum Wait : uint8_t { WAIT_NONE, WAIT_WALK, WAIT_TEXT, WAIT_FRAMES, WAIT_CHOICE, WAIT_CARD, WAIT_CAMERA };

  // Schutz gegen Endlosschleifen ohne blockierenden Befehl: nach so vielen
  // Befehlen gibt der Interpreter den Frame ab und macht im nächsten weiter.
  constexpr uint8_t STEPS_PER_FRAME = 32;

  // Ein Ablauf: Vordergrund (Skript, sperrt die Eingabe) oder Hintergrund
  // (routine in game.adv, läuft neben dem Spiel, ruht während Skripten).
  struct Context {
    uint24_t pc = NONE24;
    // Rücksprünge: call ruft ein Unterprogramm auf, ROOM das Entry-Skript des
    // neuen Raums. Die nötige Tiefe berechnet advc.py (CALL_DEPTH).
    uint24_t stack[CALL_DEPTH];
    uint8_t sp = 0;
    uint24_t origin = NONE24;  // Anfang des Ablaufs (Hintergrund: zum Wiederfinden für stop)
    Wait wait = WAIT_NONE;
    uint8_t waitActor;
    uint16_t waitFrames;
  };
  // Vordergrund (Verbskripte, Szenen) und Hintergrundabläufe (routine): im
  // Original Raumskripte, die nebeneinander laufen – etwa die Ratten-Wache und
  // der Papagei in der Stadtstraße. Sie ruhen, solange der Vordergrund läuft.
  constexpr uint8_t ROUTINES = 2;
  Context fg, bg[ROUTINES];

  void stopAll() {
    for (Context& c : bg) c.pc = NONE24;
  }

  void begin(Context& x, uint24_t address) {
    x.pc = x.origin = address;
    x.sp = 0;
    x.wait = WAIT_NONE;
  }

  bool condition(uint8_t kind, uint8_t index) {
    // „open“: Objektzustand ≠ 0 (Türen: 0 = zu, 1 = auf, wie im Original)
    uint8_t k = kind & ~COND_NOT;
    bool v = k == COND_HAS     ? World::has(index)
             : k == COND_OPEN  ? (World::objects[index] & OBJ_STATE) != 0
             : k == COND_HOVER ? Ui::hovered() == index
                               : World::flag(index);
    return (kind & COND_NOT) ? !v : v;
  }

  void step(Context& x) {
    uint8_t op[OP_MAX_LENGTH];
    FX::readDataBytes(x.pc, op, sizeof(op));
    if (op[0] >= sizeof(OP_LENGTH)) {  // defekte Daten: Skript beenden statt Amok
      x.pc = NONE24;
      x.sp = 0;
      return;
    }
    uint24_t next = x.pc + OP_LENGTH[op[0]];

    switch (op[0]) {
      case OP_END:
        next = x.sp ? x.stack[--x.sp] : NONE24;
        break;
      case OP_CALL:
        x.stack[x.sp++] = next;  // advc.py sorgt für sp < CALL_DEPTH
        next = le24(op + 1);
        break;
      case OP_SAY:
        Ui::say(op[1], le24(op + 2));
        x.wait = WAIT_TEXT;
        break;
      case OP_WALK:
        World::walkTo(op[1], le16(op + 2), op[4]);
        x.waitActor = op[1];
        x.wait = WAIT_WALK;
        break;
      case OP_PUT:
        World::put(op[1], le16(op + 2), op[4]);
        break;
      case OP_HALT:
        World::actors[op[1]].walking = false;
        break;
      case OP_REMOVE:
        World::remove(op[1]);
        break;
      case OP_FACE:
        World::face(op[1], op[2]);
        break;
      case OP_SET:
        World::setFlag(op[1], true);
        break;
      case OP_CLEAR:
        World::setFlag(op[1], false);
        break;
      case OP_JUNLESS:
        if (!condition(op[1], op[2])) next = le24(op + 3);
        break;
      case OP_JMP:
        next = le24(op + 1);
        break;
      case OP_PICKUP:
        World::pickup(op[1]);
        break;
      case OP_LOSE:
        World::lose(op[1]);
        break;
      case OP_STATE:
        World::setState(op[1], op[2]);
        break;
      case OP_HIDE:
        World::setHidden(op[1], true);
        break;
      case OP_SHOW:
        World::setHidden(op[1], false);
        break;
      case OP_MUSIC:
        Sound::play(op[1]);
        break;
      case OP_WAIT:
        x.waitFrames = op[1];
        if (x.waitFrames) x.wait = WAIT_FRAMES;
        break;
      case OP_CHOICES:
        Ui::beginChoice();
        break;
      case OP_OPTION:
        Ui::addChoice(le24(op + 1), le24(op + 4));
        break;
      case OP_ASK:
        // Ohne sichtbare Option geht es direkt weiter (Sprung ans Dialogende).
        if (Ui::ask()) x.wait = WAIT_CHOICE;
        break;
      case OP_CARD:
        Ui::card(le24(op + 1), le24(op + 4), op[7]);
        x.wait = WAIT_CARD;
        break;
      case OP_WAITR: {
        uint16_t lo = le16(op + 1), hi = le16(op + 3);
        x.waitFrames = lo + (hi > lo ? random(hi - lo + 1) : 0);
        if (x.waitFrames) x.wait = WAIT_FRAMES;
        break;
      }
      case OP_JUNLESSPOS: {
        const ActorState& a = World::actors[op[2]];
        uint16_t p = ((op[1] & POS_Y) ? a.y : a.x) >> SUBPIXEL_SHIFT;
        bool v = (op[1] & POS_GT) ? p > le16(op + 3) : p < le16(op + 3);
        if (op[1] & COND_NOT) v = !v;
        if (!v) next = le24(op + 5);
        break;
      }
      case OP_PLAY:
        // Aus einem Hintergrundablauf: die Szene läuft im Vordergrund, der
        // Ablauf ruht derweil (update() führt ihn nur ohne Vordergrund aus).
        begin(fg, le24(op + 1));
        break;
      case OP_LETR: {
        uint8_t lo = op[2], hi = op[3];
        World::vars[op[1]] = lo + random(uint16_t(hi - lo) + 1);
        break;
      }
      case OP_SETCHARV:
        World::strings[op[1]][op[2]] = World::vars[op[3]];
        break;
      case OP_START: {
        // Läuft der Ablauf schon, beginnt er von vorn; sonst ein freier Platz.
        uint24_t a = le24(op + 1);
        Context* slot = nullptr;
        for (Context& c : bg)
          if (c.pc != NONE24 && c.origin == a) slot = &c;
        for (Context& c : bg)
          if (!slot && c.pc == NONE24) slot = &c;
        if (slot) begin(*slot, a);  // advc.py prüft, dass nie mehr als ROUTINES laufen
        break;
      }
      case OP_STOP:
        for (Context& c : bg)
          if (c.origin == le24(op + 1)) c.pc = NONE24;
        break;
      case OP_RANDOM: {
        uint8_t r = random(op[1]);
        uint8_t t[3];
        FX::readDataBytes(next + uint24_t(r) * 3, t, 3);
        next = le24(t);
        break;
      }
      case OP_COSTUME:
        World::costume(op[1], op[2]);
        break;
      case OP_PAN:
        World::pan(le16(op + 1));
        x.wait = WAIT_CAMERA;
        break;
      case OP_FLASH:
        Ui::flash(op[1]);
        x.waitFrames = op[1];
        x.wait = WAIT_FRAMES;
        break;
      case OP_LET:
        World::vars[op[1]] = op[2];
        break;
      case OP_ADD:
        World::vars[op[1]] += op[2];
        break;
      case OP_JUNLESSV: {
        uint8_t v = World::vars[op[2]], k = op[1] & ~COND_NOT;
        bool ok = k == VAR_LT ? v < op[3] : k == VAR_GT ? v > op[3] : v == op[3];
        if (op[1] & COND_NOT) ok = !ok;
        if (!ok) next = le24(op + 4);
        break;
      }
      case OP_SETSTR:
        World::readString(le24(op + 2), World::strings[op[1]], STRING_SIZE);
        break;
      case OP_SETCHAR:
        World::strings[op[1]][op[2]] = op[3];
        break;
      case OP_ROOM:
        // Raum laden, Spielfigur an die Ankunftsstelle, dann das Entry-Skript
        // des Raums ausführen und danach hier weitermachen. Ein Hintergrund-
        // ablauf gehört zum alten Raum und endet (wie Raumskripte im Original).
        stopAll();
        World::loadRoom(op[1]);
        if (le16(op + 2) != 0xFFFF) {
          World::put(PLAYER, le16(op + 2), op[4]);
          World::face(PLAYER, op[5]);
        }
        x.stack[x.sp++] = next;
        next = World::roomRec.entry;
        break;
    }
    x.pc = next;
  }
}

void Script::start(uint24_t address) {
  begin(fg, address);
}

void Script::stopRoutine() {
  stopAll();
}

bool Script::running() {
  return fg.pc != NONE24;
}

void Script::chosen(uint24_t target) {
  if (fg.wait != WAIT_CHOICE) return;
  fg.pc = target;
  fg.wait = WAIT_NONE;
}

namespace {
  // Einen Ablauf weiterführen: Warten prüfen, dann Befehle bis zum nächsten
  // blockierenden Befehl.
  void run(Context& ctx) {
    switch (ctx.wait) {
      case WAIT_WALK:
        if (World::isWalking(ctx.waitActor)) return;
        break;
      case WAIT_TEXT:
        if (Ui::talking()) return;
        break;
      case WAIT_FRAMES:
        if (--ctx.waitFrames) return;
        break;
      case WAIT_CHOICE:
        return;  // chosen() setzt fort
      case WAIT_CARD:
        if (Ui::showingCard()) return;
        break;
      case WAIT_CAMERA:
        if (World::panning()) return;
        break;
      case WAIT_NONE:
        break;
    }
    ctx.wait = WAIT_NONE;
    for (uint8_t n = STEPS_PER_FRAME; n && ctx.pc != NONE24 && ctx.wait == WAIT_NONE; --n) step(ctx);
  }
}

void Script::update() {
  run(fg);
  // Der Hintergrund ruht, solange ein Skript läuft (Zwischensequenz, Dialog).
  for (Context& c : bg)
    if (fg.pc == NONE24 && c.pc != NONE24) run(c);
}
