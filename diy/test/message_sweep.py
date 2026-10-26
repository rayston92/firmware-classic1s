"""Send every message type the client library knows to the wallet, once each,
with the simplest valid contents, and watch for crashes.

Run inside its own network namespace (`unshare -rn`), it starts its own
wallet (a DEBUG_LINK=1 test build, so a test phrase can be loaded):
  python message_sweep.py <elf> <outdir>

Prompts are answered with Cancel (nothing is ever confirmed). After every
message the wallet must still be running and answer a Ping. A crash is
recorded with the message that caused it, and the wallet is restarted.
"""
import fcntl, os, socket, struct, sys, time

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
flags = struct.unpack("16sH14x", fcntl.ioctl(s, 0x8913, struct.pack("16sH14x", b"lo", 0)))[1]
fcntl.ioctl(s, 0x8914, struct.pack("16sH14x", b"lo", flags | 1))

ELF, OUT = sys.argv[1], sys.argv[2]
os.environ["E2E_OUT"] = OUT
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Wallet
from trezorlib import messages as M, protobuf
from trezorlib.debuglink import TrezorClientDebugLink, load_device
from trezorlib.transport.udp import UdpTransport

PHRASE = "all all all all all all all all all all all all"
SKIP_PREFIX = ("DebugLink",)
SKIP = {"WipeDevice", "FirmwareErase", "FirmwareUpload", "SelfTest", "RebootToBootloader", "BixinReboot",
        "LoadDevice", "BixinLoadDevice", "Cancel", "Initialize", "GetFeatures", "Ping", "ButtonAck",
        "PinMatrixAck", "PassphraseAck", "EntropyAck"}


def default_value(field):
    t = field.type
    if field.repeated:
        return []
    if isinstance(t, str) and hasattr(M, t):
        t = getattr(M, t)
    if isinstance(t, str):
        return {"uint32": 0, "uint64": 0, "sint32": 0, "sint64": 0, "bool": False,
                "bytes": b"\x00" * 32, "string": "x"}.get(t, 0)
    if isinstance(t, type) and issubclass(t, protobuf.MessageType):
        return build(t)
    try:
        return list(t)[0]  # enum
    except Exception:
        return 0


def build(cls):
    kwargs = {}
    for f in cls.FIELDS.values():
        if f.required:
            kwargs[f.name] = default_value(f)
    return cls(**kwargs)


def resolve_types():
    # Field types are stored as names in some library versions.
    for name in dir(M):
        cls = getattr(M, name)
        if isinstance(cls, type) and issubclass(cls, protobuf.MessageType):
            for f in cls.FIELDS.values():
                if isinstance(f.type, str) and hasattr(M, f.type):
                    f.type = getattr(M, f.type)


def start():
    w = Wallet(ELF, None, "sweep")
    time.sleep(1)
    c = TrezorClientDebugLink(UdpTransport("127.0.0.1:54935"))
    c.open()
    import threading
    th = threading.Thread(target=lambda: load_device(c, PHRASE, pin=None, passphrase_protection=False, label="sweep"))
    th.start()
    for _ in range(20):
        th.join(0.5)
        if not th.is_alive():
            break
        w.press("right")
    c.init_device()
    return w, c


w, c = start()
names = sorted(n for n in dir(M) if isinstance(getattr(M, n), type) and issubclass(getattr(M, n), protobuf.MessageType)
               and getattr(M, n).MESSAGE_WIRE_TYPE is not None)
only = os.environ.get("SWEEP_ONLY")
if only:
    names = [n for n in names if n in only.split(",")]
crashes, answered = [], {}
progress = open(os.path.join(OUT, "progress.txt"), "w")
for name in names:
    if name in SKIP or name.startswith(SKIP_PREFIX):
        continue
    try:
        msg = build(getattr(M, name))
    except Exception as e:
        answered[name] = f"not built: {e}"
        continue
    import threading
    box = {}

    def exchange():
        try:
            c._raw_write(msg)
            resp = c._raw_read()
            steps = 0
            while isinstance(resp, (M.ButtonRequest, M.PinMatrixRequest, M.PassphraseRequest)) and steps < 5:
                steps += 1
                c._raw_write(M.Cancel())
                resp = c._raw_read()
            box["resp"] = resp
        except Exception as e:
            box["resp"] = e

    th = threading.Thread(target=exchange, daemon=True)
    th.start()
    th.join(20)
    if th.is_alive():
        answered[name] = "NO ANSWER within 20 s (wallet stuck)"
    else:
        resp = box["resp"]
        answered[name] = type(resp).__name__ + (f": {resp.message}" if isinstance(resp, M.Failure) else "")
    progress.write(f"{name}: {answered[name]}\n")
    progress.flush()
    if th.is_alive():
        # No reply. Is the wallet frozen, or did it just not answer this message?
        try:
            w.shot("noreply-" + name)
        except Exception:
            pass
        c2 = TrezorClientDebugLink(UdpTransport("127.0.0.1:54935"))
        pb = {}
        pt = threading.Thread(target=lambda: (c2.open(), pb.update(ok=(c2.ping("alive?") == "alive?"))), daemon=True)
        pt.start()
        pt.join(10)
        if pb.get("ok"):
            answered[name] = "no reply to this message; wallet still answers"
            progress.write(f"{name}: {answered[name]}\n")
            progress.flush()
            c = c2
            continue
        crashes.append((name, "frozen" if w.proc.poll() is None else f"exited {w.proc.poll()}"))
        print(f"FROZEN or exited after {name}: screen saved", flush=True)
        try:
            w.stop()
        except Exception:
            pass
        w, c = start()
        continue
    time.sleep(0.2)
    alive = w.proc.poll() is None
    if alive:
        pb = {}
        pt = threading.Thread(target=lambda: pb.update(ok=(c.ping("alive?") == "alive?")), daemon=True)
        pt.start()
        pt.join(10)
        alive = bool(pb.get("ok"))
    if not alive:
        try:
            w.shot("noanswer-" + name)
        except Exception:
            pass
        code = w.proc.poll()
        crashes.append((name, code))
        print(f"CRASH or hang after {name} (exit {code})", flush=True)
        try:
            w.stop()
        except Exception:
            pass
        w, c = start()

with open(os.path.join(OUT, "sweep.txt"), "w") as f:
    for k, v in sorted(answered.items()):
        f.write(f"{k}: {v}\n")
print(f"{len(answered)} message types sent; {len(crashes)} crashes/hangs: {crashes}")
