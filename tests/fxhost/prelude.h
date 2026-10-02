// Host-Prüfstand: wird jeder Übersetzungseinheit vorangestellt (-include).
//
// Die Engine verlässt sich auf das AVR-Speicherlayout: Strukturen ohne
// Füllbytes und einen 3-Byte-Typ __uint24. Beides bildet dieser Vorspann
// nach. Alle Systemheader, die Prüfstand und Shims brauchen, stehen VOR dem
// pragma pack, damit deren Layout unverändert bleibt.
#pragma once

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <fstream>
#include <sstream>
#include <iostream>

struct HostU24 {
  uint8_t b[3];
  HostU24() = default;
  constexpr HostU24(uint32_t v) : b{uint8_t(v), uint8_t(v >> 8), uint8_t(v >> 16)} {}
  constexpr operator uint32_t() const { return b[0] | (uint32_t(b[1]) << 8) | (uint32_t(b[2]) << 16); }
  HostU24& operator+=(uint32_t d) { return *this = HostU24(uint32_t(*this) + d); }
  HostU24& operator-=(uint32_t d) { return *this = HostU24(uint32_t(*this) - d); }
  HostU24& operator++() { return *this += 1; }
};
static_assert(sizeof(HostU24) == 3, "HostU24 muss 3 Byte groß sein");
#define __uint24 HostU24

#pragma pack(1)
