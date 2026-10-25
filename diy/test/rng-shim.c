// Test-only: make /dev/hwrng point somewhere else, and optionally lie about its device number.
//   RNGSHIM_TARGET=/dev/zero      open this instead of /dev/hwrng ("missing" -> ENOENT)
//   RNGSHIM_FAKE_RDEV=1           report 10:183 for that file descriptor
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
static int fake_fd = -1;
static int wrap_open(int (*real)(const char *, int, ...), const char *p, int flags, mode_t mode) {
  if (p && strcmp(p, "/dev/hwrng") == 0 && getenv("RNGSHIM_TARGET")) {
    const char *t = getenv("RNGSHIM_TARGET");
    if (strcmp(t, "missing") == 0) { errno = ENOENT; return -1; }
    int fd = real(t, flags, mode);
    if (getenv("RNGSHIM_FAKE_RDEV")) fake_fd = fd;
    return fd;
  }
  return real(p, flags, mode);
}
int open(const char *p, int flags, ...) { static int (*r)(const char *, int, ...); if (!r) r = dlsym(RTLD_NEXT, "open");
  mode_t m = 0; if (flags & O_CREAT) { va_list a; va_start(a, flags); m = va_arg(a, int); va_end(a); } return wrap_open(r, p, flags, m); }
int open64(const char *p, int flags, ...) { static int (*r)(const char *, int, ...); if (!r) r = dlsym(RTLD_NEXT, "open64");
  mode_t m = 0; if (flags & O_CREAT) { va_list a; va_start(a, flags); m = va_arg(a, int); va_end(a); } return wrap_open(r, p, flags, m); }
static void fix(int fd, struct stat *st) { if (fd == fake_fd && fd >= 0) { st->st_rdev = makedev(10, 183); st->st_mode = (st->st_mode & ~S_IFMT) | S_IFCHR; } }
int fstat(int fd, struct stat *st) { static int (*r)(int, struct stat *); if (!r) r = dlsym(RTLD_NEXT, "fstat"); int x = r(fd, st); if (!x) fix(fd, st); return x; }
int fstat64(int fd, struct stat64 *st) { static int (*r)(int, struct stat64 *); if (!r) r = dlsym(RTLD_NEXT, "fstat64"); int x = r(fd, st); if (!x && fd == fake_fd && fd >= 0) { st->st_rdev = makedev(10, 183); st->st_mode = (st->st_mode & ~S_IFMT) | S_IFCHR; } return x; }
// RNGSHIM_SYSFS=/path/to/fake   read /sys/... paths from that tree instead (fopen, readlink)
#include <stdio.h>
#include <unistd.h>
static const char *sysrewrite(const char *p, char *buf, size_t len) {
  const char *root = getenv("RNGSHIM_SYSFS");
  if (root && p && strncmp(p, "/sys/", 5) == 0) { snprintf(buf, len, "%s%s", root, p + 4); return buf; }
  return p;
}
FILE *fopen(const char *p, const char *m) { static FILE *(*r)(const char *, const char *); if (!r) r = dlsym(RTLD_NEXT, "fopen");
  char b[1024]; return r(sysrewrite(p, b, sizeof b), m); }
FILE *fopen64(const char *p, const char *m) { static FILE *(*r)(const char *, const char *); if (!r) r = dlsym(RTLD_NEXT, "fopen64");
  char b[1024]; return r(sysrewrite(p, b, sizeof b), m); }
ssize_t readlink(const char *p, char *out, size_t len) { static ssize_t (*r)(const char *, char *, size_t); if (!r) r = dlsym(RTLD_NEXT, "readlink");
  char b[1024]; return r(sysrewrite(p, b, sizeof b), out, len); }
