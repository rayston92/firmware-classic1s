/*
 * DIY build: read the joystick and keys of a Raspberry Pi screen HAT
 * (Linux side; see buttons_evdev.c for how they map to the firmware).
 *
 * The keys are turned into ordinary Linux key presses by the `gpio-key`
 * overlay in /boot/firmware/config.txt, so this file only has to read
 * key events. Mapping, same as the arrow keys in the normal emulator:
 *   Left  (or Esc)    = cancel / no
 *   Right (or Enter)  = confirm / yes
 *   Up, Down          = scroll
 *
 * Licensed like the rest of the emulator: GNU LGPL v3 or later.
 */

#include <linux/input.h>

#include "evdev_keys.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define LINUX_KEY_ESC 1
#define LINUX_KEY_ENTER 28
#define LINUX_KEY_UP 103
#define LINUX_KEY_LEFT 105
#define LINUX_KEY_RIGHT 106
#define LINUX_KEY_DOWN 108

#define MAX_INPUTS 16
#define RESCAN_SECONDS 2

/* Each gpio-key line in config.txt is its own input device, and the kernel
 * may create them after the wallet has started, so keep looking for new ones
 * every couple of seconds instead of scanning once. */
static int input_fds[MAX_INPUTS];
static dev_t input_devs[MAX_INPUTS];
static int input_count = 0;
static time_t last_scan = 0;
static bool warned = false;
static unsigned pressed = 0;

#define BITS_PER_LONG (sizeof(long) * 8)
#define NLONGS(x) (((x) + BITS_PER_LONG - 1) / BITS_PER_LONG)
static bool test_bit(int bit, const unsigned long *array) {
  return (array[bit / BITS_PER_LONG] >> (bit % BITS_PER_LONG)) & 1;
}

static bool already_open(dev_t dev) {
  for (int i = 0; i < input_count; i++) {
    if (input_devs[i] == dev) return true;
  }
  return false;
}

static void drop_input(int i) {
  close(input_fds[i]);
  input_count--;
  input_fds[i] = input_fds[input_count];
  input_devs[i] = input_devs[input_count];
}

static void scan_inputs(void) {
  last_scan = time(NULL);
  DIR *dir = opendir("/dev/input");
  if (!dir) return;
  struct dirent *entry;
  while ((entry = readdir(dir)) && input_count < MAX_INPUTS) {
    if (strncmp(entry->d_name, "event", 5) != 0) continue;
    char path[300];
    snprintf(path, sizeof(path), "/dev/input/%s", entry->d_name);
    struct stat st;
    if (stat(path, &st) != 0 || !S_ISCHR(st.st_mode) ||
        already_open(st.st_rdev)) {
      continue;
    }
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) continue;
    unsigned long keys[NLONGS(KEY_MAX + 1)];
    memset(keys, 0, sizeof(keys));
    if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(keys)), keys) >= 0 &&
        (test_bit(LINUX_KEY_LEFT, keys) || test_bit(LINUX_KEY_RIGHT, keys) ||
         test_bit(LINUX_KEY_UP, keys) || test_bit(LINUX_KEY_DOWN, keys) ||
         test_bit(LINUX_KEY_ENTER, keys) || test_bit(LINUX_KEY_ESC, keys))) {
      input_fds[input_count] = fd;
      input_devs[input_count] = st.st_rdev;
      input_count++;
    } else {
      close(fd);
    }
  }
  closedir(dir);
  if (input_count == 0 && !warned) {
    fprintf(stderr,
            "No keys found. Check the gpio-key lines in config.txt and that "
            "this program may read /dev/input (group 'input').\n");
    warned = true;
  }
}

static unsigned bit_for(int code) {
  switch (code) {
    case LINUX_KEY_LEFT:
    case LINUX_KEY_ESC:
      return DIY_KEY_NO;
    case LINUX_KEY_RIGHT:
    case LINUX_KEY_ENTER:
      return DIY_KEY_YES;
    case LINUX_KEY_UP:
      return DIY_KEY_UP;
    case LINUX_KEY_DOWN:
      return DIY_KEY_DOWN;
    default:
      return 0;
  }
}

unsigned diy_keys_pressed(void) {
  if (time(NULL) - last_scan >= RESCAN_SECONDS) scan_inputs();

  for (int i = 0; i < input_count; i++) {
    struct input_event ev;
    for (;;) {
      ssize_t n = read(input_fds[i], &ev, sizeof(ev));
      if (n < 0 && errno == ENODEV) {
        /* The device went away; forget it and pick it up again later. */
        drop_input(i);
        i--;
        break;
      }
      if (n != (ssize_t)sizeof(ev)) break;
      if (ev.type != EV_KEY) continue;
      unsigned bit = bit_for(ev.code);
      if (!bit) continue;
      if (ev.value) {
        pressed |= bit; /* 1 = press, 2 = auto-repeat */
      } else {
        pressed &= ~bit;
      }
    }
  }

  return pressed;
}
