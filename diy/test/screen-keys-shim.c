// Test-only LD_PRELOAD shim: pretends a SH1106 framebuffer and a gpio-keys
// input device exist, backed by a plain file and a FIFO. Lets the DIY_HAT
// build run on a machine without the Raspberry Pi hardware.
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>

// event0 behaves like the joystick device (arrows + Enter), event1 like the
// KEY3 device, which only reports Esc.
static int fake_fb = -1, fake_in[8] = {-1, -1, -1, -1, -1, -1, -1, -1};
static int fake_in_esc[8];

static const char *rewrite(const char *path, int *kind) {
  static char buf[512];
  *kind = 0;
  if (!path) return path;
  if (strcmp(path, "/dev/fb1") == 0) { *kind = 1; return getenv("SHIM_FB"); }
  if (strncmp(path, "/dev/fb", 7) == 0) { *kind = -1; return path; }
  if (strncmp(path, "/dev/input/", 11) == 0) {
    snprintf(buf, sizeof buf, "%s/%s", getenv("SHIM_INPUT_DIR"), path + 11);
    *kind = 2; return buf;
  }
  return path;
}

#define WRAP_OPEN(name)                                                   \
  int name(const char *path, int flags, ...) {                            \
    static int (*real)(const char *, int, ...);                           \
    if (!real) real = dlsym(RTLD_NEXT, #name);                            \
    mode_t mode = 0;                                                      \
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags);               \
      mode = va_arg(ap, int); va_end(ap); }                               \
    int kind; const char *p = rewrite(path, &kind);                       \
    if (kind == -1) { errno = ENOENT; return -1; }                        \
    int fd = real(p, flags, mode);                                        \
    if (kind == 1) fake_fb = fd;                                          \
    if (kind == 2 && strstr(p, "event") && fd >= 0 && fd < 1024) {         \
      for (int i = 0; i < 8; i++) if (fake_in[i] < 0) {                     \
        fake_in[i] = fd; fake_in_esc[i] = strstr(p, "event1") != 0; break; } \
    }                                                                     \
    return fd;                                                            \
  }
#include <errno.h>
WRAP_OPEN(open)
WRAP_OPEN(open64)

int close(int fd) {
  static int (*real)(int);
  if (!real) real = dlsym(RTLD_NEXT, "close");
  for (int i = 0; i < 8; i++) if (fake_in[i] == fd) fake_in[i] = -1;
  return real(fd);
}

// The FIFOs standing in for input devices are reported as character devices.
static void as_chr(const char *p, struct stat *st) {
  st->st_mode = (st->st_mode & ~S_IFMT) | S_IFCHR;
  st->st_rdev = makedev(13, 64 + (p[strlen(p) - 1] - '0'));
}
int stat(const char *path, struct stat *st) {
  static int (*real)(const char *, struct stat *);
  if (!real) real = dlsym(RTLD_NEXT, "stat");
  int kind; const char *p = rewrite(path, &kind);
  int r = real(p, st);
  if (!r && kind == 2) as_chr(p, st);
  return r;
}
int stat64(const char *path, struct stat64 *st) {
  static int (*real)(const char *, struct stat64 *);
  if (!real) real = dlsym(RTLD_NEXT, "stat64");
  int kind; const char *p = rewrite(path, &kind);
  int r = real(p, st);
  if (!r && kind == 2) { st->st_mode = (st->st_mode & ~S_IFMT) | S_IFCHR; st->st_rdev = makedev(13, 64 + (p[strlen(p) - 1] - '0')); }
  return r;
}

DIR *opendir(const char *path) {
  static DIR *(*real)(const char *);
  if (!real) real = dlsym(RTLD_NEXT, "opendir");
  if (path && strcmp(path, "/dev/input") == 0) return real(getenv("SHIM_INPUT_DIR"));
  return real(path);
}

int ioctl(int fd, unsigned long req, ...) {
  static int (*real)(int, unsigned long, ...);
  if (!real) real = dlsym(RTLD_NEXT, "ioctl");
  va_list ap; va_start(ap, req); void *arg = va_arg(ap, void *); va_end(ap);
  if (fd == fake_fb && fd >= 0) {
    if (req == FBIOGET_FSCREENINFO) {
      struct fb_fix_screeninfo *f = arg; memset(f, 0, sizeof *f);
      strcpy(f->id, "fb_sh1106"); f->line_length = 128 * 2; return 0;
    }
    if (req == FBIOGET_VSCREENINFO) {
      struct fb_var_screeninfo *v = arg; memset(v, 0, sizeof *v);
      v->xres = 128; v->yres = 64; v->bits_per_pixel = 16; return 0;
    }
  }
  for (int i = 0; i < 8; i++) {
    if (fd != fake_in[i] || fd < 0) continue;
    if ((req & ~IOCSIZE_MASK) != (EVIOCGBIT(EV_KEY, 0) & ~IOCSIZE_MASK)) break;
    size_t n = _IOC_SIZE(req); unsigned char *b = arg; memset(b, 0, n);
    if (fake_in_esc[i]) {
      b[KEY_ESC / 8] |= 1 << (KEY_ESC % 8);
    } else {
      int codes[] = {KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER};
      for (unsigned j = 0; j < 5; j++) b[codes[j] / 8] |= 1 << (codes[j] % 8);
    }
    return (int)n;
  }
  return real(fd, req, arg);
}
