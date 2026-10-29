/*
 * hwrng-check: make sure the wallet's random numbers come from the Raspberry
 * Pi's own hardware random number generator, set up the way Broadcom's code
 * sets it up. Runs as root, from install.sh and before every wallet start.
 *
 *   hwrng-check              check; exit 0 only if everything is right
 *   hwrng-check --test [N]   the same, then run statistical tests on N blocks
 *                            of raw output (default 1000 blocks, 2.5 MB)
 *
 * What "set up the way Broadcom's code sets it up" means
 *   Broadcom has not published a manual for this generator. Its configuration
 *   is known from Broadcom's own driver code (drivers/char/hw_random/
 *   bcm2835-rng.c in Linux, "Copyright (c) 2010-2012 Broadcom"), with the
 *   register fields named in FreeBSD's driver (sys/arm/broadcom/bcm2835/
 *   bcm2835_rng.c):
 *     RNG_CTRL   (0x0)  bit 0 RBGEN: generator on; bit 1 RBG2X: double speed;
 *                       bits 4-5, 8-15: bypass the internal jitter clock with
 *                       a divided bus clock; bits 16-27: switch individual
 *                       ring oscillators off (1 = off).
 *     RNG_STATUS (0x4)  bits 0-19: warm-up count, numbers discarded after
 *                       enabling ("the initial numbers generated are 'less
 *                       random' so will be discarded"); bits 24-31: words
 *                       waiting in the FIFO.
 *   Broadcom's code writes RNG_STATUS = 0x40000, then RNG_CTRL = RBGEN (0x1):
 *   every oscillator on, internal jitter clock, normal speed. That code only
 *   runs if nothing enabled the generator earlier, and the Raspberry Pi boot
 *   firmware may enable it first. So this program reads RNG_CTRL itself and
 *   accepts exactly 0x00000001. It only reads: it maps the registers
 *   read-only and never changes them. With any other value it stops, and the
 *   wallet does not start. (The randomness the wallet uses is the hardware
 *   numbers hashed together with Linux's getrandom(), so rewriting the
 *   register would add little, and it would be the only place this setup
 *   goes around the kernel's driver.)
 *
 * It also checks that the generator Linux hands out on /dev/hwrng is this
 * Broadcom block (driver "bcm2835-rng", device tree "brcm,bcm2835-rng") and
 * not some other source.
 *
 * Licensed under the GNU GPL v3 or later.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <libgen.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define DRIVER "bcm2835-rng"
#define COMPATIBLE "brcm,bcm2835-rng"

#define RNG_CTRL 0x0
#define RNG_STATUS 0x4
#define RNG_RBGEN 0x1

/* Test hooks: point these at a fake tree to exercise the program off a Pi. */
static const char *sysfs = "/sys";
static const char *procfs = "/proc";
static const char *devmem = "/dev/mem";
static const char *hwrng = "/dev/hwrng";

static void fail(const char *fmt, const char *arg) {
  fprintf(stderr, "hwrng-check: ");
  fprintf(stderr, fmt, arg);
  fprintf(stderr, "\nThe wallet will not start without the Raspberry Pi's "
                  "hardware random number generator.\n");
  exit(1);
}

static int read_line(const char *path, char *out, size_t len) {
  FILE *f = fopen(path, "r");
  if (!f) return -1;
  if (!fgets(out, (int)len, f)) out[0] = 0;
  fclose(f);
  out[strcspn(out, "\n")] = 0;
  return 0;
}

/* The generator's name, e.g. "3f104000.rng", after checking who drives it. */
static void check_identity(char *name, size_t len) {
  char path[512], link[512];
  snprintf(path, sizeof(path), "%s/class/misc/hw_random/rng_current", sysfs);
  if (read_line(path, name, len) != 0 || !name[0] || !strcmp(name, "none")) {
    fail("%s: no hardware random number generator is active", path);
  }

  snprintf(path, sizeof(path), "%s/bus/platform/devices/%s/driver", sysfs, name);
  ssize_t n = readlink(path, link, sizeof(link) - 1);
  if (n <= 0) fail("the active generator (%s) is not a platform device", name);
  link[n] = 0;
  if (strcmp(basename(link), DRIVER) != 0) {
    fail("the active generator is driven by '%s', not Broadcom's " DRIVER,
         basename(link));
  }

  snprintf(path, sizeof(path), "%s/bus/platform/devices/%s/of_node/compatible",
           sysfs, name);
  char compat[256] = {0};
  FILE *f = fopen(path, "r");
  size_t got = f ? fread(compat, 1, sizeof(compat) - 1, f) : 0;
  if (f) fclose(f);
  int found = 0;
  for (size_t i = 0; i < got; i += strlen(compat + i) + 1) {
    if (!strcmp(compat + i, COMPATIBLE)) found = 1;
  }
  if (!found) fail("the generator's device tree entry is not " COMPATIBLE "%s", "");
}

/* Physical address of the generator's registers, from /proc/iomem.
 *
 * Current kernels label the region claimed by the driver with the device
 * name followed by the device-tree node name, e.g.
 *     3f104000-3f10400f : rng@7e104000
 *       3f104000-3f10400f : 3f104000.rng rng@7e104000
 * Older kernels write just "3f104000.rng". Both are accepted: the label must
 * be the device name, alone or followed by a space. */
static unsigned long find_base(const char *name) {
  char path[512], line[512];
  snprintf(path, sizeof(path), "%s/iomem", procfs);
  FILE *f = fopen(path, "r");
  if (!f) fail("cannot read %s", path);
  size_t nlen = strlen(name);
  unsigned long base = 0, end = 0;
  while (fgets(line, sizeof(line), f)) {
    line[strcspn(line, "\n")] = 0;
    char *label = strstr(line, " : ");
    if (!label) continue;
    label += 3;
    if (strncmp(label, name, nlen) != 0 || (label[nlen] != 0 && label[nlen] != ' ')) {
      continue;
    }
    char *p = line + strspn(line, " ");
    base = strtoul(p, &p, 16);
    if (*p == '-') end = strtoul(p + 1, NULL, 16);
    break;
  }
  fclose(f);
  if (!base) {
    fail("cannot find the registers of %s in /proc/iomem (run this as root, "
         "with sudo)", name);
  }
  if (end && end < base + 0xb) {
    fail("the register range of %s in /proc/iomem is too small", name);
  }
  /* A device-tree device is named after its address, e.g. 3f104000.rng:
   * the two must agree. */
  char *dot = NULL;
  unsigned long named = strtoul(name, &dot, 16);
  if (dot && *dot == '.' && dot != name && named != base) {
    fail("the registers in /proc/iomem do not match the generator's name (%s)", name);
  }
  return base;
}

/* Read-only mapping: this program never writes to the generator. */
static const volatile uint32_t *map_regs(unsigned long base) {
  int fd = open(devmem, O_RDONLY | O_SYNC | O_CLOEXEC);
  if (fd < 0) fail("cannot open %s to read the generator's settings", devmem);
  long page = sysconf(_SC_PAGESIZE);
  unsigned long page_base = base & ~((unsigned long)page - 1);
  void *map = mmap(NULL, (size_t)page, PROT_READ, MAP_SHARED, fd,
                   (off_t)page_base);
  close(fd);
  if (map == MAP_FAILED) fail("cannot map the generator's registers (%s)", strerror(errno));
  return (const volatile uint32_t *)((const volatile uint8_t *)map +
                                     (base - page_base));
}

static void sleep_ms(long ms) {
  struct timespec ts = {ms / 1000, (ms % 1000) * 1000000L};
  nanosleep(&ts, NULL);
}

static void check_settings(const volatile uint32_t *regs) {
  int wait_ms = 1000;
  uint32_t ctrl = regs[RNG_CTRL / 4];
  if (ctrl != RNG_RBGEN) {
    char v[16];
    snprintf(v, sizeof(v), "0x%08x", ctrl);
    fail("the generator is not set up the way Broadcom's code sets it up "
         "(RNG_CTRL=%s, expected 0x00000001: on, all oscillators, internal "
         "jitter clock, normal speed). This program only checks and does not "
         "change it",
         v);
  }
  printf("Generator settings: RNG_CTRL=0x%08x (on, all oscillators, internal "
         "jitter clock, normal speed), as Broadcom's code sets it.\n", ctrl);

  /* It must be producing: words should show up in its FIFO. */
  for (int i = 0; i < wait_ms / 10; i++) {
    if (regs[RNG_STATUS / 4] >> 24) return;
    sleep_ms(10);
  }
  fail("the generator is on but produces no numbers%s", "");
}

/* ---- FIPS 140-2 statistical tests (as in rngtest), 20000-bit blocks ---- */

#define BLOCK_BYTES 2500

static int fips_block_ok(const uint8_t *b) {
  int ones = 0, poker[16] = {0};
  int runs[2][7] = {{0}};
  int run_bit = -1, run_len = 0, longest = 0;

  for (int i = 0; i < BLOCK_BYTES; i++) {
    ones += __builtin_popcount(b[i]);
    poker[b[i] >> 4]++;
    poker[b[i] & 15]++;
    for (int k = 7; k >= 0; k--) {
      int bit = (b[i] >> k) & 1;
      if (bit == run_bit) {
        run_len++;
      } else {
        if (run_len) runs[run_bit][run_len > 6 ? 6 : run_len]++;
        run_bit = bit;
        run_len = 1;
      }
      if (run_len > longest) longest = run_len;
    }
  }
  runs[run_bit][run_len > 6 ? 6 : run_len]++;

  if (ones <= 9725 || ones >= 10275) return 0; /* monobit */
  long sum = 0;
  for (int i = 0; i < 16; i++) sum += (long)poker[i] * poker[i];
  double x = 16.0 / 5000.0 * (double)sum - 5000.0;
  if (x <= 2.16 || x >= 46.17) return 0; /* poker */
  static const int lo[7] = {0, 2315, 1114, 527, 240, 103, 103};
  static const int hi[7] = {0, 2685, 1386, 723, 384, 209, 209};
  for (int bit = 0; bit < 2; bit++) {
    for (int len = 1; len <= 6; len++) {
      if (runs[bit][len] < lo[len] || runs[bit][len] > hi[len]) return 0; /* runs */
    }
  }
  if (longest >= 26) return 0; /* long run */
  return 1;
}

static int run_tests(int blocks) {
  int fd = open(hwrng, O_RDONLY | O_CLOEXEC);
  if (fd < 0) fail("cannot open %s", hwrng);
  uint8_t buf[BLOCK_BYTES];
  int failed = 0;
  printf("Testing %d blocks (%d bytes) straight from the generator...\n", blocks,
         blocks * BLOCK_BYTES);
  for (int i = 0; i < blocks; i++) {
    size_t got = 0;
    while (got < sizeof(buf)) {
      ssize_t n = read(fd, buf + got, sizeof(buf) - got);
      if (n < 0 && errno == EINTR) continue;
      if (n <= 0) fail("reading %s failed", hwrng);
      got += (size_t)n;
    }
    if (!fips_block_ok(buf)) failed++;
  }
  close(fd);
  /* A perfect source fails about 1 block in 1000 by chance. */
  int limit = blocks / 200 + 1;
  printf("FIPS 140-2 tests: %d of %d blocks passed, %d failed.\n", blocks - failed,
         blocks, failed);
  if (failed > limit) {
    printf("RESULT: FAILED. More than %d failed. Do not use this board for a "
           "wallet.\n", limit);
    return 1;
  }
  printf("RESULT: OK. Up to %d failures are normal for a good generator.\n", limit);
  return 0;
}

int main(int argc, char **argv) {
  setvbuf(stdout, NULL, _IOLBF, 0); /* keep the log in order with stderr */
  const char *e;
  if ((e = getenv("HWRNG_CHECK_SYSFS"))) sysfs = e;
  if ((e = getenv("HWRNG_CHECK_PROCFS"))) procfs = e;
  if ((e = getenv("HWRNG_CHECK_DEVMEM"))) devmem = e;
  if ((e = getenv("HWRNG_CHECK_HWRNG"))) hwrng = e;

  int test_blocks = 0;
  if (argc > 1 && !strcmp(argv[1], "--test")) {
    test_blocks = argc > 2 ? atoi(argv[2]) : 1000;
    if (test_blocks < 1) test_blocks = 1000;
  } else if (argc > 1) {
    fprintf(stderr, "usage: %s [--test [blocks]]\n", argv[0]);
    return 2;
  }

  char name[256];
  check_identity(name, sizeof(name));
  unsigned long base = find_base(name);
  printf("Generator: %s, driver " DRIVER ", registers at 0x%08lx.\n", name, base);
  check_settings(map_regs(base));

  if (test_blocks) return run_tests(test_blocks);
  return 0;
}
