#include "Sound.h"

namespace {
  // Der Puffer muss mindestens einen Frame überbrücken. Das engste Fenster im
  // Thema sind 16 aufeinanderfolgende Noten mit zusammen 135 ms, 32 Einträge
  // reichen also mit großem Abstand.
  constexpr uint8_t RING_SIZE = 32;  // Zweierpotenz
  constexpr uint8_t RING_MASK = RING_SIZE - 1;

  NoteRec ring[RING_SIZE];
  volatile uint8_t head;       // schreibt nur update()
  volatile uint8_t tail;       // schreibt nur der Interrupt
  volatile uint8_t remaining;  // Rest-Millisekunden der laufenden Note
  volatile uint16_t currentOcr;
  volatile bool active;

  uint24_t table;
  uint8_t trackCount;
  uint8_t currentTrack = NONE8;
  uint24_t trackStart;
  uint16_t trackLength;
  bool looping;
  uint24_t nextNote;
  uint16_t notesLeft;
}

void Sound::begin(uint24_t musicTable, uint8_t musicCount) {
  table = musicTable;
  trackCount = musicCount;

  // Timer3: CTC, Takt/8 = 2 MHz, Pin-Toggle wird pro Note zugeschaltet.
  TCCR3A = 0;
  TCCR3B = _BV(WGM32) | _BV(CS31);

  // Timer1: CTC, Takt/64 = 250 kHz, Compare bei 250 → 1 kHz.
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11) | _BV(CS10);
  OCR1A = 249;
  TCNT1 = 0;
  TIMSK1 = _BV(OCIE1A);
}

void Sound::stop() {
  active = false;  // ab hier rührt der Interrupt nichts mehr an
  currentTrack = NONE8;
  TCCR3A = 0;
  currentOcr = 0;
  remaining = 0;
  head = tail = 0;
  notesLeft = 0;
  looping = false;
}

void Sound::play(uint8_t track) {
  // Läuft das Stück schon, nicht von vorn beginnen – wie im Original, das vor
  // startSound mit isSoundRunning prüft (Bar ↔ Küche).
  if (track == currentTrack && playing()) return;
  stop();
  if (track >= trackCount) return;
  currentTrack = track;

  uint24_t address;
  FX::readDataObject(table + uint24_t(track) * 3, address);
  TrackRec rec;
  FX::readDataObject(address, rec);

  trackStart = address + sizeof(TrackRec);
  trackLength = rec.count;
  looping = rec.loop;
  nextNote = trackStart;
  notesLeft = trackLength;

  update();
  active = true;
}

void Sound::update() {
  uint8_t free = (tail - head - 1) & RING_MASK;
  while (free) {
    if (!notesLeft) {
      if (!looping || !trackLength) return;
      nextNote = trackStart;
      notesLeft = trackLength;
    }
    uint8_t n = free;
    if (n > RING_SIZE - head) n = RING_SIZE - head;  // nur zusammenhängend schreiben
    if (n > notesLeft) n = notesLeft;
    FX::readDataBytes(nextNote, reinterpret_cast<uint8_t*>(&ring[head]), n * sizeof(NoteRec));
    nextNote += uint24_t(n) * sizeof(NoteRec);
    notesLeft -= n;
    free -= n;
    head = (head + n) & RING_MASK;  // erst nach dem Schreiben freigeben
  }
}

bool Sound::playing() {
  return active && (notesLeft || head != tail || remaining);
}

ISR(TIMER1_COMPA_vect) {
  if (!active) return;
  if (remaining) {
    --remaining;
    return;
  }
  uint8_t t = tail;
  if (t == head) {  // Track zu Ende oder Puffer leer
    TCCR3A = 0;
    currentOcr = 0;
    return;
  }
  uint16_t ocr = ring[t].ocr;
  remaining = ring[t].ms - 1;
  tail = (t + 1) & RING_MASK;

  if (!ocr) {
    TCCR3A = 0;
  } else if (ocr != currentOcr) {
    // Zähler zurücksetzen: Läge TCNT3 schon über dem neuen OCR3A, liefe
    // er erst bis 0xFFFF und es gäbe einen 30-ms-Aussetzer.
    OCR3A = ocr;
    TCNT3 = 0;
    TCCR3A = _BV(COM3A0);
  }
  currentOcr = ocr;
}
