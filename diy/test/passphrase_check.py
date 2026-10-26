"""Recovery phrases of every length, with and without a passphrase, against
independent derivation in Python. Against a debug-interface test build with
fresh storage (run-check.sh):
  python passphrase_check.py <ctl-dir>

For 12-, 18- and 24-word phrases, and for passphrases that are empty, plain,
long and non-English, the wallet's Bitcoin and Ethereum addresses must equal
the ones computed here with BIP-39 (passphrase normalized to NFKD, as the
standard says), BIP-32 and the coin's address format. A passphrase longer than
the wallet's limit (50 bytes) must be refused, never cut short: a wallet made
from a cut-short passphrase could not be restored anywhere else.
"""
import os, sys, threading, time, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ctl import send

from embit import bip32, bip39
from embit.bech32 import encode as segwit_encode
from embit.hashes import hash160
from eth_utils import keccak, to_checksum_address
from trezorlib import messages as M
from trezorlib.debuglink import TrezorClientDebugLink, load_device
from trezorlib.tools import parse_path
from trezorlib.transport.udp import UdpTransport

OUT = sys.argv[1]
results = []

PHRASES = {
    12: "all all all all all all all all all all all all",
    18: " ".join(["abandon"] * 17 + ["agent"]),
    24: "void come effort suffer camp survey warrior heavy shoot primary clutch crush "
        "open amazing screen patrol group space point ten exist slush involve unfold",
}
PASSPHRASES = ["", "TREZOR", "correct horse battery staple 123!@#",
               "Ünïcödé パスワード 密码", "x" * 50]
TOO_LONG = "y" * 51


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def reference(phrase, passphrase):
    seed = bip39.mnemonic_to_seed(phrase, password=unicodedata.normalize("NFKD", passphrase))
    root = bip32.HDKey.from_seed(seed)
    btc = segwit_encode("bc", 0, hash160(root.derive("m/84'/0'/0'/0/0").key.sec()))
    from embit.util import secp256k1
    pub = root.derive("m/44'/60'/0'/0/0").key
    raw = secp256k1.ec_pubkey_serialize(secp256k1.ec_pubkey_parse(pub.sec()), secp256k1.EC_UNCOMPRESSED)
    eth = to_checksum_address(keccak(raw[1:])[-20:])
    return btc, eth


client = TrezorClientDebugLink(UdpTransport("127.0.0.1:54935"))
client.open()
if client.features.initialized:
    sys.exit("start the wallet with fresh storage")


def call(msg, passphrase=None):
    """Send msg; answer a passphrase request with `passphrase`, and press
    confirm at every button request (the wallet shows the passphrase)."""
    resp = client.call_raw(msg)
    while True:
        if isinstance(resp, M.PassphraseRequest):
            if passphrase is None:
                return resp
            resp = client.call_raw(M.PassphraseAck(passphrase=unicodedata.normalize("NFKD", passphrase)))
            continue
        if isinstance(resp, M.ButtonRequest):
            stop = threading.Event()

            def presser():
                while not stop.is_set():
                    time.sleep(0.8)
                    if not stop.is_set():
                        send(OUT, "right")

            client._raw_write(M.ButtonAck())
            th = threading.Thread(target=presser, daemon=True)
            th.start()
            resp = client._raw_read()
            stop.set()
            th.join()
            continue
        return resp


def addresses(passphrase):
    call(M.Initialize())  # new session: the wallet asks for the passphrase again
    a = call(M.GetAddress(address_n=parse_path("m/84'/0'/0'/0/0"), coin_name="Bitcoin",
                          script_type=M.InputScriptType.SPENDWITNESS), passphrase)
    e = call(M.EthereumGetAddress(address_n=parse_path("m/44'/60'/0'/0/0")), passphrase)
    return getattr(a, "address", None) or repr(a), getattr(e, "address", None) or repr(e)


first = True
for words, phrase in PHRASES.items():
    if not first:
        r = call(M.WipeDevice())
        check(f"{words} words: wipe before loading", isinstance(r, M.Success), type(r).__name__)
    first = False
    client.init_device()
    load_device(client, phrase, pin=None, passphrase_protection=False, label=f"{words} words")
    client.init_device()
    got = addresses(None)
    check(f"{words} words, no passphrase: BTC and ETH addresses", got == reference(phrase, ""), " ".join(got))

    call(M.ApplySettings(use_passphrase=True))
    for p in PASSPHRASES:
        got = addresses(p)
        label = repr(p) if len(p) < 20 else repr(p[:12] + "...") + f" ({len(p.encode())} bytes)"
        check(f"{words} words, passphrase {label}: BTC and ETH addresses", got == reference(phrase, p), " ".join(got))

    # The same passphrase again gives the same wallet; a different one never does.
    a1 = addresses("TREZOR")
    a2 = addresses("trezor")
    check(f"{words} words: same passphrase, same wallet; one letter different, different wallet",
          a1 == reference(phrase, "TREZOR") and a2 == reference(phrase, "trezor") and a1 != a2)

    call(M.Initialize())
    r = call(M.GetAddress(address_n=parse_path("m/84'/0'/0'/0/0"), coin_name="Bitcoin",
                          script_type=M.InputScriptType.SPENDWITNESS), TOO_LONG)
    check(f"{words} words: 51-byte passphrase refused, not cut short", isinstance(r, M.Failure),
          getattr(r, "message", None) or getattr(r, "address", type(r).__name__))
    # The wallet still waits for a passphrase it can accept; cancel that, as an app would.
    r = call(M.Cancel())
    check(f"{words} words: waiting for the passphrase cancelled", isinstance(r, M.Failure), getattr(r, "message", ""))
    call(M.ApplySettings(use_passphrase=False))

check("wallet still answers", client.ping("ok?") == "ok?")
print(f"{sum(results)}/{len(results)} passed")
