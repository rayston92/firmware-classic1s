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
static int note_hw(int fd);
static int wrap_open(int (*real)(const char *, int, ...), const char *p, int flags, mode_t mode) {
  if (p && strcmp(p, "/dev/hwrng") == 0 && getenv("RNGSHIM_TARGET")) {
    const char *t = getenv("RNGSHIM_TARGET");
    if (strcmp(t, "missing") == 0) { errno = ENOENT; return -1; }
    int fd = real(t, flags, mode);
    if (getenv("RNGSHIM_FAKE_RDEV")) fake_fd = fd;
    return note_hw(fd);
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

// Tracing, for the entropy test (diy/test/entropy_check.py):
//   RNGSHIM_GETRANDOM=/path  answer getrandom() from this file, in order
//   RNGSHIM_LOG=/path        append "hw <offset> <len>" for every read of /dev/hwrng
//                            and "os <offset> <len>" for every getrandom() call
#include <sys/random.h>
static int hw_fd_seen = -1;
static void trace(const char *kind, long off, long len) {
  const char *p = getenv("RNGSHIM_LOG");
  if (!p) return;
  static int (*ro)(const char *, int, ...);
  if (!ro) ro = dlsym(RTLD_NEXT, "open");
  int fd = ro(p, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
  if (fd < 0) return;
  char line[80];
  int n = snprintf(line, sizeof line, "%s %ld %ld\n", kind, off, len);
  (void)!write(fd, line, (size_t)n);
  close(fd);
}
static int note_hw(int fd) { hw_fd_seen = fd; return fd; }
ssize_t read(int fd, void *buf, size_t len) {
  static ssize_t (*r)(int, void *, size_t);
  if (!r) r = dlsym(RTLD_NEXT, "read");
  if (fd != hw_fd_seen || fd < 0) return r(fd, buf, len);
  off_t off = lseek(fd, 0, SEEK_CUR);
  ssize_t n = r(fd, buf, len);
  trace("hw", (long)off, (long)n);
  return n;
}
ssize_t getrandom(void *buf, size_t len, unsigned int flags) {
  static ssize_t (*r)(void *, size_t, unsigned int);
  static int src = -2;
  static long pos = 0;
  if (!r) r = dlsym(RTLD_NEXT, "getrandom");
  if (src == -2) {
    const char *p = getenv("RNGSHIM_GETRANDOM");
    static int (*ro)(const char *, int, ...);
    if (!ro) ro = dlsym(RTLD_NEXT, "open");
    src = p ? ro(p, O_RDONLY | O_CLOEXEC) : -1;
  }
  if (src < 0) {
    ssize_t n = r(buf, len, flags);
    trace("os", -1, (long)n);
    return n;
  }
  static ssize_t (*rd)(int, void *, size_t);
  if (!rd) rd = dlsym(RTLD_NEXT, "read");
  ssize_t n = rd(src, buf, len);
  trace("os", pos, (long)n);
  if (n > 0) pos += n;
  return n;
}
// With _FORTIFY_SOURCE the wallet calls __read_chk instead of read.
ssize_t __read_chk(int fd, void *buf, size_t len, size_t buflen) {
  (void)buflen;
  return read(fd, buf, len);
}
