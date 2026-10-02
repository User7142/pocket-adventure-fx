#include "Sound.h"

namespace {
  // The buffer must bridge at least one frame. The tightest window in the
  // theme is 16 consecutive notes totalling 135 ms, so 32 entries
  // are more than enough.
  constexpr uint8_t RING_SIZE = 32;  // power of two
  constexpr uint8_t RING_MASK = RING_SIZE - 1;

  NoteRec ring[RING_SIZE];
  volatile uint8_t head;       // written only by update()
  volatile uint8_t tail;       // written only by the interrupt
  volatile uint8_t remaining;  // remaining milliseconds of the current note
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

  // Timer3: CTC, clock/8 = 2 MHz, pin toggle is enabled per note.
  TCCR3A = 0;
  TCCR3B = _BV(WGM32) | _BV(CS31);

  // Timer1: CTC, clock/64 = 250 kHz, compare at 250 → 1 kHz.
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11) | _BV(CS10);
  OCR1A = 249;
  TCNT1 = 0;
  TIMSK1 = _BV(OCIE1A);
}

void Sound::stop() {
  active = false;  // from here on the interrupt touches nothing
  currentTrack = NONE8;
  TCCR3A = 0;
  currentOcr = 0;
  remaining = 0;
  head = tail = 0;
  notesLeft = 0;
  looping = false;
}

void Sound::play(uint8_t track) {
  // If the track is already playing, do not restart it – like the original, which
  // checks isSoundRunning before startSound (bar ↔ kitchen).
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
    if (n > RING_SIZE - head) n = RING_SIZE - head;  // write contiguously only
    if (n > notesLeft) n = notesLeft;
    FX::readDataBytes(nextNote, reinterpret_cast<uint8_t*>(&ring[head]), n * sizeof(NoteRec));
    nextNote += uint24_t(n) * sizeof(NoteRec);
    notesLeft -= n;
    free -= n;
    head = (head + n) & RING_MASK;  // release only after writing
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
  if (t == head) {  // track finished or buffer empty
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
    // Reset the counter: if TCNT3 were already above the new OCR3A, it would
    // first run up to 0xFFFF, causing a 30 ms dropout.
    OCR3A = ocr;
    TCNT3 = 0;
    TCCR3A = _BV(COM3A0);
  }
  currentOcr = ocr;
}
