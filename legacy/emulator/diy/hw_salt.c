/*
 * DIY build: tie the stored wallet to this particular board.
 *
 * On a real device, part of the key that encrypts storage comes from the
 * chip's unique ID. The normal emulator uses zeros instead. Here we use the
 * Raspberry Pi's serial number, which is burned into the processor, so a
 * copy of the memory card alone is not enough to start guessing the PIN on
 * another computer: the attacker also needs this board's serial number.
 *
 * This is a small hurdle, not a secure element. Anyone holding the board
 * itself can read the serial number.
 *
 * Licensed like the rest of the emulator: GNU LGPL v3 or later.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "memzero.h"
#include "sha2.h"

static size_t read_file(const char *path, char *buf, size_t size) {
  FILE *f = fopen(path, "rb");
  if (!f) return 0;
  size_t n = fread(buf, 1, size - 1, f);
  fclose(f);
  buf[n] = 0;
  return n;
}

void diy_hw_salt(uint8_t *out, size_t len) {
  char serial[128] = {0};
  size_t n = read_file("/sys/firmware/devicetree/base/serial-number", serial,
                       sizeof(serial));
  if (n == 0) {
    /* Not a Raspberry Pi (for example a desktop test build). */
    n = read_file("/etc/machine-id", serial, sizeof(serial));
  }

  /* Expand sha256("diy-hw-salt" || serial) to the requested length. */
  uint8_t block[SHA256_DIGEST_LENGTH];
  uint8_t counter = 0;
  size_t done = 0;
  while (done < len) {
    SHA256_CTX ctx;
    sha256_Init(&ctx);
    sha256_Update(&ctx, (const uint8_t *)"diy-hw-salt", 11);
    sha256_Update(&ctx, &counter, 1);
    sha256_Update(&ctx, (const uint8_t *)serial, n);
    sha256_Final(&ctx, block);
    size_t take = len - done < sizeof(block) ? len - done : sizeof(block);
    memcpy(out + done, block, take);
    done += take;
    counter++;
  }
  memzero(block, sizeof(block));
  memzero(serial, sizeof(serial));
}
