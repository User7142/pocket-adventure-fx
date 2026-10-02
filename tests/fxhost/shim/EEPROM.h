#pragma once

struct HostEeprom {
  uint8_t data[1024];
  HostEeprom() { memset(data, 0xFF, sizeof(data)); }
  uint8_t read(uint16_t a) { return data[a]; }
  void update(uint16_t a, uint8_t v) { data[a] = v; }
};
inline HostEeprom EEPROM;
