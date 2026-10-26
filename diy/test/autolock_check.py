"""Auto-lock checks on an unlocked wallet (use unlock_then.py to unlock first).

  AUTOLOCK_PIN=<pin> python unlock_then.py <ctl-outdir> <pin> autolock_check.py

Sets the auto-lock delay to 1 minute (the shortest), then:
  1. a prompt left open past the delay stays on screen, the wallet stays
     unlocked, and confirming it still works;
  2. idle on the home screen past the delay: the wallet locks (screensaver);
  3. after unlocking again, key presses in the menu keep it unlocked past the
     delay; once they stop, it locks.
Takes about 7 minutes."""
import os, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ctl import send
from pin_entry import enter_pin
from PIL import Image
from trezorlib import messages as M
from trezorlib.client import TrezorClient
from trezorlib.tools import parse_path
from trezorlib.transport.udp import UdpTransport

OUT = sys.argv[1]
PIN = os.environ["AUTOLOCK_PIN"]  # the wallet's PIN, entered again after it locks
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def lit(path):
    return sum(1 for p in Image.open(path).convert("L").getdata() if p > 60)


class UI:
    def button_request(self, br):
        pass


t = UdpTransport("127.0.0.1:54935"); t.open()
c = TrezorClient(t, ui=UI()); c.open()


def call_confirm(msg, wait_before_confirm=0.0):
    resp = c.call_raw(msg)
    shots = []
    while isinstance(resp, M.ButtonRequest):
        c._raw_write(M.ButtonAck())
        time.sleep(1)
        shots.append(send(OUT, "shot", "prompt").strip())
        if wait_before_confirm:
            time.sleep(wait_before_confirm)
            shots.append(send(OUT, "shot", "prompt-after-wait").strip())
            wait_before_confirm = 0
        stop = threading.Event()

        def presser():
            while not stop.is_set():
                send(OUT, "right"); time.sleep(0.8)

        th = threading.Thread(target=presser, daemon=True); th.start()
        resp = c._raw_read()
        stop.set(); th.join()
    return resp, shots


r, _ = call_confirm(M.ApplySettings(auto_lock_delay_ms=60_000))
c.init_device()
check("auto-lock delay set to 1 minute", c.features.auto_lock_delay_ms == 60_000, f"{type(r).__name__} {c.features.auto_lock_delay_ms}")

# 1. A prompt left open for 100 s.
r, shots = call_confirm(M.GetAddress(address_n=parse_path("m/84h/0h/0h/0/0"), coin_name="Bitcoin",
                                     show_display=True, script_type=M.InputScriptType.SPENDWITNESS), 100)
same = len(shots) >= 2 and lit(shots[0]) == lit(shots[1])
check("prompt still on screen after 100 s", same, " ".join(os.path.basename(s) for s in shots))
check("confirming it after 100 s still works", isinstance(r, M.Address), getattr(r, "address", r))
c.init_device()
check("wallet still unlocked right after", c.features.unlocked)

# 2. Idle on the home screen.
time.sleep(80)
shot = send(OUT, "shot", "idle-80s").strip()
c.init_device()
check("locked after 80 s idle on the home screen", not c.features.unlocked, os.path.basename(shot))

# 3. Menu activity keeps it unlocked.
enter_pin(OUT, PIN)  # opens the menu after unlocking
first = send(OUT, "shot", "menu-start").strip()
for i in range(9):  # 9 x 10 s = 90 s with key presses
    time.sleep(10)
    send(OUT, "down")
send(OUT, "up", "9")
menu = send(OUT, "shot", "menu-after-90s").strip()
check("menu still open after 90 s of key presses", lit(menu) > 2000 and lit(menu) == lit(first),
      f"{os.path.basename(first)} {os.path.basename(menu)}")
time.sleep(80)
idle = send(OUT, "shot", "menu-idle-80s").strip()
c.init_device()
check("locked 80 s after the last key press", not c.features.unlocked, os.path.basename(idle))
print(f"{sum(results)}/{len(results)} passed")
