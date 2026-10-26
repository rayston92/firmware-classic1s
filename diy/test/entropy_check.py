"""Check, on the running wallet, exactly where the recovery phrase's randomness comes from.

The wallet is started with both of its randomness sources replaced by files
we know (rng-shim.so): the hardware generator (/dev/hwrng) and the kernel's
getrandom(). Every read from either source is logged. This script then
recomputes, independently in Python, the byte stream that
legacy/emulator/diy/rand_hw.c is supposed to produce from those two files:

    skip the first 1 MiB of the hardware stream (warm-up)
    block i (i = 1, 2, ...):
        hw  = SHA-256("diy-hwrng-v1" || i as 8 bytes little-endian || next 128 hardware bytes)
        out = SHA-256("diy-mix-v1" || hw || next 32 getrandom bytes)
    stream = out_1 || out_2 || ...

and checks that the wallet's recovery phrase is made from that stream:

  device  - phrase created on the wallet itself: the phrase's entropy must be
            a run of bytes from the stream.
  app     - phrase created by the app (ResetDevice): the wallet mixes 32
            stream bytes with 32 bytes from the computer,
            entropy = SHA-256(stream bytes || computer bytes)[:16 or 32].

Usage:
  python entropy_check.py streams <dir>                   # make hw-A, hw-B, os-X, os-Y
  python entropy_check.py device <hw> <os> <log> <words>  # phrase read off the screen
  python entropy_check.py app <ctl-dir> <hw> <os> <log>   # create via the app protocol, compare
"""
import hashlib, os, sys

STARTUP = 0x40000 * 4
RAW = 128


def stream(hw_path, os_path, log_path, blocks=None):
    """Recompute rand_hw.c's output from the two source files. Also check the
    trace: after the warm-up, hardware reads come 128 bytes at a time and
    getrandom 32 at a time, in step with each other."""
    hw = open(hw_path, "rb").read()
    osb = open(os_path, "rb").read()
    log = [l.split() for l in open(log_path)] if log_path else []
    hw_reads = [(int(o), int(n)) for k, o, n in log if k == "hw"]
    os_reads = [(int(o), int(n)) for k, o, n in log if k == "os"]
    # Warm-up: everything up to STARTUP read first, in order.
    pos, i = 0, 0
    while pos < STARTUP:
        o, n = hw_reads[i]
        assert o == pos, f"hardware read out of order at {o}"
        pos += n
        i += 1
    assert pos == STARTUP, f"warm-up read {pos} bytes, expected {STARTUP}"
    after = hw_reads[i:]
    assert all(n == RAW for _, n in after), f"unexpected hardware read sizes {set(n for _, n in after)}"
    assert [o for o, _ in after] == [STARTUP + RAW * j for j in range(len(after))], "hardware reads not sequential"
    assert all(n == 32 for _, n in os_reads), f"unexpected getrandom sizes {set(n for _, n in os_reads)}"
    assert [o for o, _ in os_reads] == [32 * j for j in range(len(os_reads))], "getrandom reads not sequential"
    assert len(after) == len(os_reads), f"{len(after)} hardware blocks but {len(os_reads)} getrandom calls"
    nblocks = blocks or len(after)
    out = b""
    for j in range(nblocks):
        raw = hw[STARTUP + RAW * j: STARTUP + RAW * (j + 1)]
        h = hashlib.sha256(b"diy-hwrng-v1" + (j + 1).to_bytes(8, "little") + raw).digest()
        out += hashlib.sha256(b"diy-mix-v1" + h + osb[32 * j: 32 * (j + 1)]).digest()
    return out, len(after)


def words_to_entropy(words):
    from mnemonic import Mnemonic
    m = Mnemonic("english")
    assert m.check(words), "not a valid BIP39 phrase"
    return bytes(m.to_entropy(words))


def cmd_streams(d):
    os.makedirs(d, exist_ok=True)
    for name, size in [("hw-A.bin", 4 << 20), ("hw-B.bin", 4 << 20), ("os-X.bin", 1 << 20), ("os-Y.bin", 1 << 20)]:
        open(os.path.join(d, name), "wb").write(os.urandom(size))
    print("made", d)


def cmd_device(hw, osf, log, words):
    ent = words_to_entropy(words)
    s, n = stream(hw, osf, log)
    k = s.find(ent)
    print(f"{n} blocks drawn ({len(s)} bytes); phrase entropy {ent.hex()}")
    if k < 0:
        print("FAIL: the phrase is not made from the expected stream")
        sys.exit(1)
    print(f"PASS: the phrase's entropy is stream bytes {k}..{k + len(ent) - 1} (block {k // 32 + 1}, offset {k % 32})")


def cmd_app(ctl_dir, hw, osf, log):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import threading, time
    from ctl import send
    from trezorlib import device, btc, messages as M
    from trezorlib.client import TrezorClient
    from trezorlib.tools import parse_path
    from trezorlib.transport.udp import UdpTransport
    from mnemonic import Mnemonic
    from embit import bip32, bip39

    ext = hashlib.sha256(b"computer entropy for the test").digest()
    device.os.urandom = lambda n: ext[:n]  # the 32 bytes the app sends (EntropyAck)

    class UI:
        def button_request(self, br):
            def go():
                for _ in range(3):
                    time.sleep(1.0)
                    send(ctl_dir, "shot", "reset")
                    send(ctl_dir, "right")
            threading.Thread(target=go, daemon=True).start()

        def get_pin(self, code=None):
            raise RuntimeError("PIN asked")

        def get_passphrase(self, available_on_device=False):
            return ""

    t = UdpTransport("127.0.0.1:54935"); t.open()
    c = TrezorClient(t, ui=UI())
    device.reset(c, strength=128, skip_backup=True, pin_protection=False, label="entropy test")
    time.sleep(1.5)
    xpub = btc.get_public_node(c, parse_path("m/44h/0h/0h"), coin_name="Bitcoin").node
    s, n = stream(hw, osf, log)
    m = Mnemonic("english")
    for k in range(0, len(s) - 31):
        ent = hashlib.sha256(s[k:k + 32] + ext).digest()[:16]
        root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(m.to_mnemonic(ent)))
        node = root.derive("m/44'/0'/0'")
        if node.key.sec() == xpub.public_key and node.chain_code == xpub.chain_code:
            print(f"{n} blocks drawn; device key matches phrase from SHA-256(stream[{k}:{k + 32}] || computer bytes)")
            print("PASS: phrase =", " ".join(m.to_mnemonic(ent).split()[:2]), "... (12 words)")
            return
    print("FAIL: no offset in the stream reproduces the wallet's key")
    sys.exit(1)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "streams":
        cmd_streams(sys.argv[2])
    elif cmd == "device":
        cmd_device(sys.argv[2], sys.argv[3], sys.argv[4], " ".join(sys.argv[5:]))
    elif cmd == "app":
        cmd_app(*sys.argv[2:6])
