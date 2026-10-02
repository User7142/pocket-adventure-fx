// Host test bench: prepended to every translation unit (-include).
//
// The engine relies on the AVR memory layout: structs without padding
// bytes and a 3-byte type __uint24. This prelude replicates both.
// All system headers that the test bench and shims need come BEFORE the
// pragma pack, so their layout stays unchanged.
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
static_assert(sizeof(HostU24) == 3, "HostU24 must be 3 bytes");
#define __uint24 HostU24

#pragma pack(1)
