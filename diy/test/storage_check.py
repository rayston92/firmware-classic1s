"""Open the wallet's storage file (emulator.img) independently, with the
reference Python model of the storage format (storage/tests/python), and
check how the recovery phrase is kept:

- the phrase never appears in the file as text;
- it can only be read after unlocking with the right PIN (the encryption key
  is derived from the PIN and the board's salt); a wrong PIN, or the right PIN
  with another board's salt, cannot read it.

Usage: python storage_check.py <emulator.img> <pin> "<12 words>" [serial-or-machine-id file]
"""
import hashlib, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../../storage/tests/python"))
from src import consts  # noqa: E402

consts.NORCOW_SECTOR_SIZE = 16 * 1024  # legacy firmware: two 16 KiB sectors (flash sectors 2 and 3)
from src.storage import Storage  # noqa: E402

KEY_MNEMONIC = 2 | (0x01 << 8)


def diy_hw_salt(id_bytes, length=12 + 32):
    out, counter = b"", 0
    while len(out) < length:
        out += hashlib.sha256(b"diy-hw-salt" + bytes([counter]) + id_bytes).digest()
        counter += 1
    return out[:length]


def open_storage(img, salt):
    data = open(img, "rb").read()
    sectors = [data[0x8000:0xC000], data[0xC000:0x10000]]
    s = Storage()
    s.nc._set_sectors(sectors)
    s.init(salt)
    return s


img, pin, words = sys.argv[1], sys.argv[2], sys.argv[3]
id_file = sys.argv[4] if len(sys.argv) > 4 else "/etc/machine-id"
salt = diy_hw_salt(open(id_file, "rb").read())
raw = open(img, "rb").read()

ok = True
for w in [words, words.split()[0] + " " + words.split()[1]] + words.split():
    if w.encode() in raw:
        print("FAIL: found in the file as text:", w)
        ok = False
print("phrase text in file:", "absent" if ok else "PRESENT")

s = open_storage(img, salt)
print("PIN set:", s.has_pin())
try:
    got = s.unlock(pin) and s.get(KEY_MNEMONIC)
except Exception as e:
    got = None
    print("unlock error:", e)
print("right PIN, right salt ->", "phrase matches" if got and got.decode() == words else f"FAIL ({got!r})")
ok &= bool(got) and got.decode() == words

wrong = str((int(pin) + 1) % 10 ** len(pin)).zfill(len(pin))
s = open_storage(img, salt)
print(f"wrong PIN {wrong} ->", "refused" if not s.unlock(wrong) else "FAIL: unlocked")
ok &= not open_storage(img, salt).unlock(wrong)

s = open_storage(img, diy_hw_salt(b"another board"))
r = s.unlock(pin)
print("right PIN, other board's salt ->", "refused" if not r else "FAIL: unlocked")
ok &= not r
print("PASS" if ok else "FAIL")
