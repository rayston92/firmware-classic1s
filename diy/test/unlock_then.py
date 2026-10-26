"""Unlock the wallet on its screen, go back to the home screen, then run a check.
  python unlock_then.py <ctl-outdir> <pin> <check.py> [check args...]"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ctl import send
from pin_entry import enter_pin
out, pin, check = sys.argv[1], sys.argv[2], sys.argv[3]
enter_pin(out, pin)
time.sleep(0.5)
print("after unlock:", send(out, "left").strip(), flush=True)  # leave the menu
os.execv(sys.executable, [sys.executable, "-u", os.path.join(HERE, check), out] + sys.argv[4:])
