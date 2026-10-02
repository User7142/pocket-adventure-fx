#pragma once

#include <Arduboy2.h>
#include <ArduboyFX.h>
#include "gamedata.h"

extern Arduboy2 arduboy;

constexpr uint8_t NONE8 = 0xFF;
constexpr uint24_t NONE24 = 0xFFFFFF;

// Platzhalter im Text, danach Platz + 1: String-Variable bzw. Zahl (vars).
constexpr char STRING_VAR = 0x01;
constexpr char INT_VAR = 0x03;

// Die Spielfigur ist per Konvention der erste Actor der Spielbeschreibung.
constexpr uint8_t PLAYER = 0;

enum Dir : uint8_t { DIR_RIGHT = 0, DIR_LEFT = 1, DIR_FRONT = 2 };

// Little-endian-Operanden aus einem gelesenen Befehlspuffer.
inline uint16_t le16(const uint8_t* p) { return p[0] | (uint16_t(p[1]) << 8); }
inline uint24_t le24(const uint8_t* p) { return p[0] | (uint24_t(p[1]) << 8) | (uint24_t(p[2]) << 16); }
