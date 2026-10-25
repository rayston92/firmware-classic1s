"""Run the DIY wallet (screen HAT build) headless, for the end-to-end test.

The wallet runs with LD_PRELOAD shims: a file stands in for the OLED framebuffer,
a FIFO for the joystick, /dev/urandom (reported as /dev/hwrng) for the hardware
random number generator, and a fake sysfs saying the generator is Broadcom's.
The client is OneKey's fork of trezorlib from the firmware repository, over the
wallet's local UDP port, which is exactly what the USB relay forwards to.
"""
import os, struct, subprocess, tempfile, threading, time
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.environ.get("E2E_OUT", "/tmp/e2e")


def fake_sysfs(root):
    """A /sys tree saying the active hardware generator is Broadcom's (bcm2835-rng)."""
    dev = os.path.join(root, "bus/platform/devices/3f104000.rng")
    os.makedirs(os.path.join(dev, "of_node"), exist_ok=True)
    os.makedirs(os.path.join(root, "bus/platform/drivers/bcm2835-rng"), exist_ok=True)
    os.makedirs(os.path.join(root, "class/misc/hw_random"), exist_ok=True)
    open(os.path.join(dev, "of_node/compatible"), "wb").write(b"brcm,bcm2835-rng\0")
    if not os.path.islink(os.path.join(dev, "driver")):
        os.symlink("../../../bus/platform/drivers/bcm2835-rng", os.path.join(dev, "driver"))
    open(os.path.join(root, "class/misc/hw_random/rng_current"), "w").write("3f104000.rng\n")
    return root
CODES = {"left": 105, "right": 106, "up": 103, "down": 108}
ON, OFF = (82, 180, 255), (0, 0, 0)


class Wallet:
    def __init__(self, elf, sysfs=None, name="run", state=None):
        self.work = tempfile.mkdtemp()
        sysfs = sysfs or fake_sysfs(os.path.join(self.work, "sys"))
        if state:  # start from a saved wallet (its flash storage file)
            import shutil
            shutil.copy(state, os.path.join(self.work, "emulator.img"))
        self.fb = os.path.join(self.work, "fb")
        open(self.fb, "wb").write(bytes(128 * 64 * 2))
        indir = os.path.join(self.work, "input")
        os.makedirs(indir)
        fifo = os.path.join(indir, "event0")
        os.mkfifo(fifo)
        # E2E_QEMU=1 runs an ARM64 build under qemu-user (the Pi's CPU); then
        # E2E_SHIMS must point at ARM64 builds of the two shims.
        preload = os.environ.get("E2E_SHIMS") or f"{HERE}/screen-keys-shim.so {HERE}/rng-shim.so"
        env = dict(
            os.environ,
            SHIM_FB=self.fb,
            SHIM_INPUT_DIR=indir,
            RNGSHIM_TARGET="/dev/urandom",
            RNGSHIM_FAKE_RDEV="1",
            RNGSHIM_SYSFS=sysfs,
        )
        self.log = open(os.path.join(self.work, "wallet.log"), "w")
        elf = os.path.abspath(elf)
        if os.environ.get("E2E_QEMU"):
            cmd = ["qemu-aarch64", "-L", "/usr/aarch64-linux-gnu", "-E", "LD_PRELOAD=" + preload, elf]
        else:
            cmd = [elf]
            env["LD_PRELOAD"] = preload
        self.proc = subprocess.Popen(cmd, cwd=self.work, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.keys = os.open(fifo, os.O_WRONLY)  # waits until the wallet opens its keys
        self.out = os.path.join(SCRATCH, name)
        os.makedirs(self.out, exist_ok=True)
        self.shots = 0
        self.lock = threading.Lock()
        time.sleep(1.0)

    def press(self, key, hold=0.12):
        t = time.time()
        ev = lambda v: struct.pack("qqHHi", int(t), 0, 1, CODES[key], v) + struct.pack("qqHHi", int(t), 0, 0, 0, 0)
        with self.lock:
            os.write(self.keys, ev(1))
            time.sleep(hold)
            os.write(self.keys, ev(0))

    def screen(self):
        data = open(self.fb, "rb").read()
        img = Image.new("RGB", (128, 64))
        img.putdata([ON if data[2 * j] or data[2 * j + 1] else OFF for j in range(128 * 64)])
        return img

    def shot(self, label):
        self.shots += 1
        path = os.path.join(self.out, f"{self.shots:03d}-{label}.png")
        self.screen().resize((256, 128), Image.NEAREST).save(path)
        return path

    def save_state(self, path):
        import shutil
        shutil.copy(os.path.join(self.work, "emulator.img"), path)

    def stop(self):
        self.proc.terminate()
        self.proc.wait()
        os.close(self.keys)
        return open(os.path.join(self.work, "wallet.log")).read()


class PressingUI:
    """Answers the wallet's button requests the way a person would: look, then press."""

    def __init__(self, wallet, plan=None):
        self.w = wallet
        self.plan = plan  # function(button_request) -> list of keys
        self.requests = []

    def button_request(self, br):
        code = getattr(br, "code", None)
        self.requests.append(code)
        keys = self.plan(br) if self.plan else ["right"]

        def go():
            time.sleep(0.8)
            for k in keys:
                self.w.shot(f"req{len(self.requests)}-{k}")
                self.w.press(k)
                time.sleep(0.7)

        threading.Thread(target=go, daemon=True).start()

    def get_pin(self, code=None):
        raise RuntimeError("PIN asked unexpectedly")

    def get_passphrase(self, available_on_device=False):
        return ""
