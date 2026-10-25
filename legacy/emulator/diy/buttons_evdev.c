/*
 * DIY build: report the HAT's keys to the firmware.
 * The keys are read from Linux in evdev_keys.c; this file only maps them,
 * using the same convention as the SDL emulator (a cleared bit = pressed).
 *
 * Licensed like the rest of the emulator: GNU LGPL v3 or later.
 */

#include "buttons.h"
#include "evdev_keys.h"

uint16_t buttonRead(void) {
  unsigned keys = diy_keys_pressed();
  uint16_t state = 0;
  if (keys & DIY_KEY_NO) state |= BTN_PIN_NO;
  if (keys & DIY_KEY_YES) state |= BTN_PIN_YES;
  if (keys & DIY_KEY_UP) state |= BTN_PIN_UP;
  if (keys & DIY_KEY_DOWN) state |= BTN_PIN_DOWN;
  return (uint16_t)~state;
}
