"""Test diy/hwrng-check.c against a simulated Raspberry Pi.

  python hwrng_check_test.py [path/to/hwrng-check]   (compiles ../hwrng-check.c if omitted)

The fake tree copies what a Pi Zero 2 W running a current Raspberry Pi OS
kernel shows: the generator 3f104000.rng driven by bcm2835-rng, and in
/proc/iomem the region labelled "3f104000.rng rng@7e104000" (device name,
then device-tree node name) under a parent entry "rng@7e104000". Older
kernels label it just "3f104000.rng"; both must work. /dev/mem is a sparse
file with the two registers at their physical address.
"""
import os, shutil, struct, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 0x3F104000
NAME = "3f104000.rng"

IOMEM_CURRENT = f"""00000000-1effffff : System RAM
  00210000-0116ffff : Kernel code
3f007000-3f007eff : dma@7e007000
3f104000-3f10400f : rng@7e104000
  3f104000-3f10400f : {NAME} rng@7e104000
3f200000-3f2000b3 : gpio@7e200000
"""
IOMEM_OLD = f"""3f104000-3f10400f : {NAME}
"""
IOMEM_DECOY = """3f104000-3f10400f : 3f104000.rngx
"""
IOMEM_WRONG_ADDR = f"""3f105000-3f10500f : {NAME} rng@7e104000
"""


def tree(root, iomem, ctrl=1, status=0x04000000, driver="bcm2835-rng", hw=None):
    sysfs, procfs = os.path.join(root, "sys"), os.path.join(root, "proc")
    dev = os.path.join(sysfs, "bus/platform/devices", NAME)
    os.makedirs(os.path.join(dev, "of_node"))
    os.makedirs(os.path.join(sysfs, "bus/platform/drivers", driver))
    os.makedirs(os.path.join(sysfs, "class/misc/hw_random"))
    os.makedirs(procfs)
    open(os.path.join(dev, "of_node/compatible"), "wb").write(b"brcm,bcm2835-rng\0")
    os.symlink(f"../../../bus/platform/drivers/{driver}", os.path.join(dev, "driver"))
    open(os.path.join(sysfs, "class/misc/hw_random/rng_current"), "w").write(NAME + "\n")
    open(os.path.join(procfs, "iomem"), "w").write(iomem)
    mem = os.path.join(root, "mem")
    with open(mem, "wb") as f:  # sparse: only the register page holds data
        f.truncate(BASE + 0x1000)
        f.seek(BASE)
        f.write(struct.pack("<II", ctrl, status))
    hwrng = os.path.join(root, "hwrng")
    open(hwrng, "wb").write(hw if hw is not None else os.urandom(2500 * 1000))
    return dict(os.environ, HWRNG_CHECK_SYSFS=sysfs, HWRNG_CHECK_PROCFS=procfs,
                HWRNG_CHECK_DEVMEM=mem, HWRNG_CHECK_HWRNG=hwrng)


def run(prog, env, *args):
    p = subprocess.run([prog, *args], env=env, capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout + p.stderr


CASES = [
    # name, tree options, extra args, expected exit code, text that must appear
    ("current kernel's /proc/iomem labels", dict(iomem=IOMEM_CURRENT), [], 0, "RNG_CTRL=0x00000001"),
    ("older kernel's /proc/iomem labels", dict(iomem=IOMEM_OLD), [], 0, "RNG_CTRL=0x00000001"),
    ("statistical test on good data", dict(iomem=IOMEM_CURRENT), ["--test"], 0, "RESULT: OK"),
    ("statistical test on a stuck source", dict(iomem=IOMEM_CURRENT, hw=bytes(2500 * 1000)), ["--test"], 1, "RESULT: FAILED"),
    ("other generator setting (double speed)", dict(iomem=IOMEM_CURRENT, ctrl=0x3), [], 1, "RNG_CTRL=0x00000003"),
    ("generator switched off", dict(iomem=IOMEM_CURRENT, ctrl=0x0), [], 1, "RNG_CTRL=0x00000000"),
    ("generator on but no numbers", dict(iomem=IOMEM_CURRENT, status=0), [], 1, "produces no numbers"),
    ("another driver", dict(iomem=IOMEM_CURRENT, driver="iproc-rng200"), [], 1, "not Broadcom's bcm2835-rng"),
    ("a similar name is not taken for it", dict(iomem=IOMEM_DECOY), [], 1, "cannot find the registers"),
    ("address and name disagree", dict(iomem=IOMEM_WRONG_ADDR), [], 1, "do not match"),
]


def main():
    if len(sys.argv) > 1:
        prog = os.path.abspath(sys.argv[1])
    else:
        prog = os.path.join(tempfile.mkdtemp(), "hwrng-check")
        subprocess.run(["cc", "-O2", "-Wall", "-o", prog, os.path.join(HERE, "..", "hwrng-check.c")], check=True)
    ok_all = True
    for name, opts, args, want_rc, want_text in CASES:
        root = tempfile.mkdtemp()
        try:
            rc, out = run(prog, tree(root, **opts), *args)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        ok = rc == want_rc and want_text in out
        ok_all &= ok
        last = [l for l in out.splitlines() if l.strip()]
        print(("PASS " if ok else "FAIL ") + f"{name}: exit {rc}  [{last[-1] if last else ''}]" +
              ("" if ok else f"\n     expected exit {want_rc} and '{want_text}'\n     output: {out!r}"), flush=True)
    print(f"{'all' if ok_all else 'NOT all'} {len(CASES)} cases passed")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
