/*
 * DIY build: random numbers from the hardware random number generator inside
 * the board's own chip. This is the only source of randomness for the wallet,
 * including the recovery phrase.
 *
 * The normal emulator uses a fixed formula that starts from zero, so every
 * emulator creates the same recovery phrase. That is fine for automated tests
 * and never fine for a wallet.
 *
 * Source
 *   /dev/hwrng: the Linux driver for the chip's true random number generator,
 *   which samples the timing jitter of free-running ring oscillators in the
 *   silicon. On a Raspberry Pi Zero 2 W this is the Broadcom RNG block, driver
 *   bcm2835-rng. On the screen HAT build the wallet refuses any other driver,
 *   and the device node must really be the hardware generator (character
 *   device 10:183); /dev/urandom or a file is refused.
 *
 *   Broadcom publishes no manual for this generator. Its own driver code sets
 *   it up with all oscillators on, normal speed, and the first 0x40000 numbers
 *   discarded after it is switched on. diy/hwrng-check (run as root before the
 *   wallet starts) verifies those register settings. On top of that, this file
 *   throws away the first 0x40000 words (1 MiB) it reads itself, so the
 *   warm-up has happened no matter who switched the generator on.
 *
 * Health tests (NIST SP 800-90B, section 4.4), on every byte read
 *   Repetition Count Test   the same value 6 times in a row fails.
 *   Adaptive Proportion Test  in each window of 512 bytes, the first value
 *                           occurring 62 times or more fails.
 *   Cutoffs assume at least 4 bits of min-entropy per byte, with a false
 *   alarm rate of 2^-20. At start-up, 1 MiB is tested and thrown away (the
 *   warm-up above; SP 800-90B asks for at least 1024 samples).
 *
 * Conditioning
 *   Each 32-byte output block is SHA-256 over 128 tested bytes from the
 *   hardware generator (4 times more input than output), a counter and a
 *   label. That block is then hashed once more together with 32 bytes from
 *   the Linux kernel's generator. Hashing can only add unpredictability, so
 *   the result is at least as strong as the hardware alone; the kernel input
 *   is a second layer, never a replacement.
 *
 * Failure
 *   No device, a short read or a failed health test stops the wallet at
 *   once: the reason goes to the log, "Wallet stopped" to the screen, and the
 *   program waits there until the Pi is restarted. There is no fallback.
 *
 * Licensed like the rest of the emulator: GNU LGPL v3 or later.
 */

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

#include "memzero.h"
#include "rand.h"
#include "sha2.h"

#define HWRNG_PATH "/dev/hwrng"
#define HWRNG_MAJOR 10
#define HWRNG_MINOR 183

#define RCT_CUTOFF 6       /* 1 + ceil(20 / 4) */
#define APT_WINDOW 512
#define APT_CUTOFF 62      /* SP 800-90B table 2, H = 4, W = 512 */
#define WARMUP_WORDS 0x40000 /* Broadcom's warm-up count, in 32-bit words */
#define STARTUP_BYTES (WARMUP_WORDS * 4)
#define RAW_PER_BLOCK 128  /* 4 x oversampling for 32 output bytes */

static int hw_fd = -1;

/* Repetition Count Test state */
static int rct_last = -1;
static int rct_count = 0;
/* Adaptive Proportion Test state */
static int apt_first = -1;
static int apt_count = 0;
static int apt_seen = 0;

static uint8_t pool[SHA256_DIGEST_LENGTH];
static size_t pool_left = 0;
static uint64_t block_counter = 0;

/* Provided by oled_fb.c on the screen HAT build; absent otherwise. */
void diy_show_fatal(const char *line1, const char *line2)
    __attribute__((weak));
void diy_show_status(const char *title, const char *line1, const char *line2)
    __attribute__((weak));

static void __attribute__((noreturn)) fail(const char *why) {
  memzero(pool, sizeof(pool));
  pool_left = 0;
  fprintf(stderr,
          "Hardware random number generator: %s\n"
          "The wallet stops here rather than use weak randomness.\n",
          why);
  fflush(stderr);
  if (diy_show_fatal) diy_show_fatal("Random number", "generator failed");
  /* Stay stopped, with the message on screen, until the Pi is restarted.
   * Exiting would only make systemd start the wallet again. */
  for (;;) pause();
}

static void health_test(uint8_t b) {
  if (b == rct_last) {
    if (++rct_count >= RCT_CUTOFF) fail("repetition count test failed");
  } else {
    rct_last = b;
    rct_count = 1;
  }

  if (apt_seen == 0) {
    apt_first = b;
    apt_count = 1;
  } else if (b == apt_first) {
    if (++apt_count >= APT_CUTOFF) fail("adaptive proportion test failed");
  }
  if (++apt_seen >= APT_WINDOW) apt_seen = 0;
}

static void read_hw(uint8_t *buf, size_t len) {
  size_t done = 0;
  while (done < len) {
    ssize_t n = read(hw_fd, buf + done, len - done);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) fail("read failed");
    done += (size_t)n;
  }
  for (size_t i = 0; i < len; i++) health_test(buf[i]);
}

/* The kernel driver behind the active generator, e.g. "bcm2835-rng". */
static void active_driver(const char *name, char *out, size_t len) {
  char path[300], link[300];
  out[0] = 0;
  snprintf(path, sizeof(path), "/sys/bus/platform/devices/%s/driver", name);
  ssize_t n = readlink(path, link, sizeof(link) - 1);
  if (n <= 0) return;
  link[n] = 0;
  const char *base = strrchr(link, '/');
  snprintf(out, len, "%s", base ? base + 1 : link);
}

static void open_hw(void) {
  hw_fd = open(HWRNG_PATH, O_RDONLY | O_CLOEXEC);
  if (hw_fd < 0) {
    fail("cannot open " HWRNG_PATH " (missing, or no permission to read it)");
  }
  struct stat st;
  if (fstat(hw_fd, &st) != 0 || !S_ISCHR(st.st_mode) ||
      major(st.st_rdev) != HWRNG_MAJOR || minor(st.st_rdev) != HWRNG_MINOR) {
    fail(HWRNG_PATH " is not the hardware random number generator device");
  }

  char current[64] = {0};
  FILE *f = fopen("/sys/class/misc/hw_random/rng_current", "r");
  if (f) {
    if (!fgets(current, sizeof(current), f)) current[0] = 0;
    fclose(f);
    current[strcspn(current, "\n")] = 0;
  }
  if (current[0] == 0 || strcmp(current, "none") == 0) {
    fail("no hardware generator is active in the kernel");
  }
  char driver[64];
  active_driver(current, driver, sizeof(driver));
#if DIY_HAT
  /* On the Pi it must be Broadcom's generator and nothing else. */
  if (strcmp(driver, "bcm2835-rng") != 0) {
    fail("the active generator is not the Raspberry Pi's Broadcom generator");
  }
#endif
  fprintf(stderr, "Randomness: hardware generator %s (driver %s), health tests on.\n",
          current, driver[0] ? driver : "unknown");

  /* Warm-up and start-up test: 1 MiB, health-tested, then thrown away. */
  if (diy_show_status) {
    diy_show_status("Starting", "Preparing random", "numbers...");
  }
  uint8_t chunk[4096];
  for (size_t done = 0; done < STARTUP_BYTES; done += sizeof(chunk)) {
    read_hw(chunk, sizeof(chunk));
  }
  memzero(chunk, sizeof(chunk));
}

static void refill(void) {
  if (hw_fd < 0) open_hw();

  uint8_t raw[RAW_PER_BLOCK];
  uint8_t hw_block[SHA256_DIGEST_LENGTH];
  uint8_t os_bytes[SHA256_DIGEST_LENGTH];
  SHA256_CTX ctx;

  read_hw(raw, sizeof(raw));
  block_counter++;
  sha256_Init(&ctx);
  sha256_Update(&ctx, (const uint8_t *)"diy-hwrng-v1", 12);
  sha256_Update(&ctx, (const uint8_t *)&block_counter, sizeof(block_counter));
  sha256_Update(&ctx, raw, sizeof(raw));
  sha256_Final(&ctx, hw_block);

  size_t got = 0;
  while (got < sizeof(os_bytes)) {
    ssize_t n = getrandom(os_bytes + got, sizeof(os_bytes) - got, 0);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) fail("kernel generator unavailable");
    got += (size_t)n;
  }

  sha256_Init(&ctx);
  sha256_Update(&ctx, (const uint8_t *)"diy-mix-v1", 10);
  sha256_Update(&ctx, hw_block, sizeof(hw_block));
  sha256_Update(&ctx, os_bytes, sizeof(os_bytes));
  sha256_Final(&ctx, pool);
  pool_left = sizeof(pool);

  memzero(raw, sizeof(raw));
  memzero(hw_block, sizeof(hw_block));
  memzero(os_bytes, sizeof(os_bytes));
  memzero(&ctx, sizeof(ctx));
}

/* Overrides the weak version in rand.c. */
void random_buffer(uint8_t *buf, size_t len) {
  while (len > 0) {
    if (pool_left == 0) refill();
    size_t take = len < pool_left ? len : pool_left;
    uint8_t *src = pool + sizeof(pool) - pool_left;
    memcpy(buf, src, take);
    memzero(src, take); /* never hand out the same bytes twice */
    buf += take;
    len -= take;
    pool_left -= take;
  }
}

uint32_t random32(void) {
  uint32_t r;
  random_buffer((uint8_t *)&r, sizeof(r));
  return r;
}

/* Only used by the debug interface's test commands. Deliberately ignored. */
void random_reseed(const uint32_t value) { (void)value; }
