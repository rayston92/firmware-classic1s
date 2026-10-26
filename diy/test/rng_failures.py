"""Start the production wallet with a broken or fake random number source and
check that it stops with "Random number generator failed", stays stopped, and
never gets as far as making a recovery phrase.

  python rng_failures.py <wallet program> <outdir>

Set E2E_QEMU=1 and E2E_SHIMS (as for harness.py) to test the ARM64 build.
One screenshot per case is saved in <outdir>.

Cases: no /dev/hwrng; /dev/urandom or a plain file in its place; a generator
stuck at zero; one alternating 0x55/0xAA (never the same byte twice in a
row); one with only 2 bits of randomness per byte; one that works through
the warm-up and then gets stuck, or runs out; no active generator in the
kernel; a generator other than the Pi's Broadcom one; the kernel's own
generator failing. A working generator is the control.
"""
import os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import fake_sysfs
from PIL import Image

ELF = os.path.abspath(sys.argv[1])
OUT = sys.argv[2]
QEMU = bool(os.environ.get("E2E_QEMU"))
PRELOAD = os.environ.get("E2E_SHIMS") or f"{HERE}/screen-keys-shim.so {HERE}/rng-shim.so"
os.makedirs(OUT, exist_ok=True)

MIB = 1 << 20
files = tempfile.mkdtemp()


def make(name, data):
    p = os.path.join(files, name)
    open(p, "wb").write(data)
    return p


good = os.urandom(MIB)
alt = make("alternating", b"\x55\xaa" * MIB)
then_zero = make("random-then-zero", good + bytes(MIB))
then_end = make("random-then-ends", good + os.urandom(128))
low = make("low-entropy", bytes(b % 4 for b in os.urandom(2 * MIB)))


def sysfs_variant(root, current="3f104000.rng\n", driver="bcm2835-rng"):
    fake_sysfs(root)
    open(os.path.join(root, "class/misc/hw_random/rng_current"), "w").write(current)
    if driver != "bcm2835-rng":
        os.makedirs(os.path.join(root, "bus/platform/drivers", driver), exist_ok=True)
        link = os.path.join(root, "bus/platform/devices/3f104000.rng/driver")
        os.unlink(link)
        os.symlink(f"../../../bus/platform/drivers/{driver}", link)
    return root


HW = {"RNGSHIM_TARGET": "/dev/urandom", "RNGSHIM_FAKE_RDEV": "1"}  # a working "hardware" generator
CASES = [
    # name, environment, fake /sys options, reason the wallet must give (None: must start normally)
    ("missing", {"RNGSHIM_TARGET": "missing"}, {}, "cannot open /dev/hwrng"),
    ("urandom-instead", {"RNGSHIM_TARGET": "/dev/urandom"}, {}, "is not the hardware random number generator device"),
    ("file-instead", {"RNGSHIM_TARGET": alt}, {}, "is not the hardware random number generator device"),
    ("stuck-at-zero", {"RNGSHIM_TARGET": "/dev/zero", "RNGSHIM_FAKE_RDEV": "1"}, {}, "repetition count test failed"),
    ("alternating-55-aa", {"RNGSHIM_TARGET": alt, "RNGSHIM_FAKE_RDEV": "1"}, {}, "adaptive proportion test failed"),
    ("2-bits-per-byte", {"RNGSHIM_TARGET": low, "RNGSHIM_FAKE_RDEV": "1"}, {}, "test failed"),
    ("stuck-after-warm-up", {"RNGSHIM_TARGET": then_zero, "RNGSHIM_FAKE_RDEV": "1"}, {}, "repetition count test failed"),
    ("runs-out-after-warm-up", {"RNGSHIM_TARGET": then_end, "RNGSHIM_FAKE_RDEV": "1"}, {}, "read failed"),
    ("no-active-generator", HW, {"current": "none\n"}, "no hardware generator is active"),
    ("other-driver", HW, {"driver": "iproc-rng200"}, "not the Raspberry Pi's Broadcom generator"),
    ("kernel-generator-fails", dict(HW, RNGSHIM_GETRANDOM="/dev/null"), {}, "kernel generator unavailable"),
    ("control-working", HW, {}, None),
]


def screen(fb):
    data = open(fb, "rb").read()
    img = Image.new("RGB", (128, 64))
    img.putdata([(82, 180, 255) if data[2 * j] or data[2 * j + 1] else (0, 0, 0) for j in range(128 * 64)])
    return img


ok_all = True
for name, env_extra, sysargs, reason in CASES:
    work = tempfile.mkdtemp()
    sysfs = sysfs_variant(os.path.join(work, "sys"), **sysargs)
    fb = os.path.join(work, "fb")
    open(fb, "wb").write(bytes(128 * 64 * 2))
    indir = os.path.join(work, "input")
    os.makedirs(indir)
    fifo = os.path.join(indir, "event0")
    os.mkfifo(fifo)
    keep = os.open(fifo, os.O_RDWR)  # so the wallet never waits for its keys
    env = dict(os.environ, SHIM_FB=fb, SHIM_INPUT_DIR=indir, RNGSHIM_SYSFS=sysfs)
    env.update(env_extra)
    if QEMU:
        cmd = ["qemu-aarch64", "-L", "/usr/aarch64-linux-gnu", "-E", "LD_PRELOAD=" + PRELOAD, ELF]
    else:
        cmd = [ELF]
        env["LD_PRELOAD"] = PRELOAD
    with open(os.path.join(work, "log"), "w") as log:
        p = subprocess.Popen(cmd, cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
        time.sleep(12 if QEMU else 5)
        alive = p.poll() is None  # stopped means waiting on the message, not exited and restarted
        screen(fb).resize((256, 128), Image.NEAREST).save(os.path.join(OUT, f"{name}.png"))
        p.terminate()
        p.wait()
    os.close(keep)
    text = open(os.path.join(work, "log")).read()
    stopped = "The wallet stops here rather than use weak randomness" in text
    if reason is None:
        ok = alive and not stopped and "health tests on" in text
    else:
        ok = alive and stopped and reason in text
    ok_all &= ok
    said = [l for l in text.splitlines() if l.startswith(("Hardware random", "Randomness"))]
    print(("PASS " if ok else "FAIL ") + f"{name:24s} {said[-1] if said else text[-200:]!r}", flush=True)
    shutil.rmtree(work, ignore_errors=True)
shutil.rmtree(files, ignore_errors=True)
print(f"{'all' if ok_all else 'NOT all'} {len(CASES)} cases passed")
sys.exit(0 if ok_all else 1)
