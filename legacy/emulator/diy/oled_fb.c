/*
 * DIY build: draw the 128x64 screen on a small OLED attached to a
 * Raspberry Pi, through the kernel's framebuffer driver for the SH1106
 * (enabled with `dtoverlay=sh1106-spi` in /boot/firmware/config.txt).
 *
 * Replaces the SDL window used by the normal emulator build.
 *
 * Environment variables:
 *   DIY_OLED_FB       framebuffer device to use (default: the first one whose
 *                     driver name contains "sh1106" or "ssd130x", e.g. /dev/fb1)
 *   DIY_OLED_ROTATE   set to 180 if the picture is upside down
 *
 * This file is part of the DIY build patches and is licensed like the rest of
 * the emulator: GNU LGPL v3 or later.
 */

#include "oled.h"

#include <fcntl.h>
#include <linux/fb.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int fb_fd = -1;
static int fb_width = 0;
static int fb_height = 0;
static int fb_line_length = 0;
static int fb_bytes_per_pixel = 2;
static bool rotate180 = false;

static int open_fb(const char *path) {
  int fd = open(path, O_RDWR | O_CLOEXEC);
  if (fd < 0) return -1;
  return fd;
}

static int find_oled_fb(void) {
  const char *env = getenv("DIY_OLED_FB");
  if (env && *env) {
    int fd = open_fb(env);
    if (fd < 0) {
      perror("DIY_OLED_FB");
      exit(1);
    }
    return fd;
  }
  char path[32];
  for (int i = 0; i < 8; i++) {
    snprintf(path, sizeof(path), "/dev/fb%d", i);
    int fd = open_fb(path);
    if (fd < 0) continue;
    struct fb_fix_screeninfo fix;
    /* fbtft's fb_sh1106 calls itself "fb_sh1106"; the newer DRM driver for
     * the same chip (ssd130x) calls its framebuffer "ssd130xdrmfb". */
    if (ioctl(fd, FBIOGET_FSCREENINFO, &fix) == 0 &&
        (strstr(fix.id, "sh1106") || strstr(fix.id, "ssd130x"))) {
      return fd;
    }
    close(fd);
  }
  fprintf(stderr,
          "No SH1106 screen found. Check that config.txt contains "
          "'dtparam=spi=on' and 'dtoverlay=sh1106-spi', then reboot.\n");
  exit(1);
}

void oledInit(void) {
  /* May already be open: a message can be shown before the firmware starts. */
  if (fb_fd < 0) {
    fb_fd = find_oled_fb();
    /* Switch the screen on. The DRM driver (ssd130x) leaves a framebuffer
     * switched off until something unblanks it or the text console takes it
     * over; with the HDMI framebuffer at /dev/fb0 the console stays there,
     * and everything drawn here would never reach the OLED. Harmless for
     * fbtft's fb_sh1106, which simply sends "display on". */
    ioctl(fb_fd, FBIOBLANK, FB_BLANK_UNBLANK);
  }

  struct fb_var_screeninfo var;
  struct fb_fix_screeninfo fix;
  if (ioctl(fb_fd, FBIOGET_VSCREENINFO, &var) != 0 ||
      ioctl(fb_fd, FBIOGET_FSCREENINFO, &fix) != 0) {
    perror("Failed to read screen information");
    exit(1);
  }
  fb_width = (int)var.xres;
  fb_height = (int)var.yres;
  fb_line_length = (int)fix.line_length;
  fb_bytes_per_pixel = (int)(var.bits_per_pixel / 8);
  if (fb_bytes_per_pixel < 1 || fb_bytes_per_pixel > 4) {
    fprintf(stderr, "Unsupported screen format (%u bits per pixel)\n",
            var.bits_per_pixel);
    exit(1);
  }

  const char *rot = getenv("DIY_OLED_ROTATE");
  rotate180 = rot && atoi(rot) == 180;

  oledClear();
  oledRefresh();
}

void oledRefresh(void) {
  /* Same marker as the normal emulator when the debug interface is on. */
  oledInvertDebugLink();

  const uint8_t *buffer = oledGetBuffer();
  static uint8_t pixels[OLED_HEIGHT][OLED_WIDTH];

  /* Unpack the firmware's page buffer the same way the SDL emulator does. */
  for (size_t i = 0; i < OLED_BUFSIZE; i++) {
    int x = (OLED_BUFSIZE - 1 - i) % OLED_WIDTH;
    int y = (OLED_BUFSIZE - 1 - i) / OLED_WIDTH * 8 + 7;
    for (uint8_t shift = 0; shift < 8; shift++, y--) {
      bool on = (buffer[i] >> shift) & 1;
      if (rotate180) {
        pixels[OLED_HEIGHT - 1 - y][OLED_WIDTH - 1 - x] = on;
      } else {
        pixels[y][x] = on;
      }
    }
  }

  /* Any non-zero pixel is lit on the SH1106 framebuffer. */
  static uint8_t row[OLED_WIDTH * 4];
  int width = fb_width < OLED_WIDTH ? fb_width : OLED_WIDTH;
  int height = fb_height < OLED_HEIGHT ? fb_height : OLED_HEIGHT;
  for (int y = 0; y < height; y++) {
    for (int x = 0; x < width; x++) {
      memset(&row[x * fb_bytes_per_pixel], pixels[y][x] ? 0xFF : 0x00,
             fb_bytes_per_pixel);
    }
    ssize_t n = pwrite(fb_fd, row, (size_t)(width * fb_bytes_per_pixel),
                       (off_t)y * fb_line_length);
    if (n < 0) {
      perror("Failed to write to the screen");
      break;
    }
  }

  oledInvertDebugLink();
}

/* Called by rand_hw.c when the wallet refuses to go on: say so on the
 * screen, because the person holding the device can't see the log. */
void diy_show_status(const char *title, const char *line1, const char *line2) {
  /* Randomness is first needed before the firmware turns the screen on. */
  if (fb_fd < 0) oledInit();
  oledClear();
  oledDrawStringCenter(OLED_WIDTH / 2, 10, title, FONT_STANDARD);
  oledDrawStringCenter(OLED_WIDTH / 2, 30, line1, FONT_STANDARD);
  oledDrawStringCenter(OLED_WIDTH / 2, 42, line2, FONT_STANDARD);
  oledRefresh();
}

void diy_show_fatal(const char *line1, const char *line2) {
  diy_show_status("Wallet stopped", line1, line2);
}

/* The SDL build polls window events here; there is no window on the device. */
void emulatorPoll(void) {}
