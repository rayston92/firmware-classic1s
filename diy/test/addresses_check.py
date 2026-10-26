"""Addresses for more networks, against independent derivation in Python.
Against a debug-interface test build with fresh storage (run-check.sh):
  python addresses_check.py <ctl-dir>

The test phrase is loaded; each address the wallet reports is compared with
one computed here from the phrase: BIP-32 (embit) for secp256k1 coins,
SLIP-10 for ed25519 coins, and each network's own address encoding.
"""
import base64, hashlib, hmac, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base58
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from embit import bip32, bip39
from embit.bech32 import Encoding as BEnc, bech32_encode, convertbits
from embit.hashes import hash160
from trezorlib import messages as M
from trezorlib.debuglink import TrezorClientDebugLink, load_device
from trezorlib.tools import parse_path
from trezorlib.transport.udp import UdpTransport

PHRASE = "all all all all all all all all all all all all"
seed = bip39.mnemonic_to_seed(PHRASE)
root = bip32.HDKey.from_seed(seed)
results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + f"  [{got}]" + ("" if ok else f" expected {want}"), flush=True)


def secp_pub(path, compressed=True):
    pub = root.derive(path).key
    if compressed:
        return pub.sec()
    from embit.util import secp256k1
    return secp256k1.ec_pubkey_serialize(secp256k1.ec_pubkey_parse(pub.sec()), secp256k1.EC_UNCOMPRESSED)


def slip10_ed25519(path):
    I = hmac.new(b"ed25519 seed", seed, hashlib.sha512).digest()
    k, c = I[:32], I[32:]
    for level in parse_path(path):
        assert level & 0x80000000, "ed25519 needs hardened levels"
        I = hmac.new(c, b"\x00" + k + struct.pack(">I", level), hashlib.sha512).digest()
        k, c = I[:32], I[32:]
    return Ed25519PrivateKey.from_private_bytes(k).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def b58check(version, payload, alphabet=base58.BITCOIN_ALPHABET):
    return base58.b58encode_check(version + payload, alphabet=alphabet).decode()


def bech32(hrp, data):
    return bech32_encode(BEnc.BECH32, hrp, convertbits(data, 8, 5))


def crc16_xmodem(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


client = TrezorClientDebugLink(UdpTransport("127.0.0.1:54935"))
client.open()
if client.features.initialized:
    sys.exit("start the wallet with fresh storage")
load_device(client, PHRASE, pin=None, passphrase_protection=False, label="address test")
client.init_device()


def ask(msg):
    r = client.call_raw(msg)
    return getattr(r, "address", None) or getattr(r, "message", None) or type(r).__name__


# Bitcoin-family (other than Bitcoin itself, which wallet_flow.py covers)
for coin, path, ver in [("Litecoin", "m/44'/2'/0'/0/0", b"\x30"), ("Dogecoin", "m/44'/3'/0'/0/0", b"\x1e")]:
    got = ask(M.GetAddress(address_n=parse_path(path), coin_name=coin, script_type=M.InputScriptType.SPENDADDRESS))
    check(f"{coin} {path}", got, b58check(ver, hash160(secp_pub(path))))
path = "m/84'/2'/0'/0/0"
got = ask(M.GetAddress(address_n=parse_path(path), coin_name="Litecoin", script_type=M.InputScriptType.SPENDWITNESS))
from embit.bech32 import encode as segwit_encode
check(f"Litecoin SegWit {path}", got, segwit_encode("ltc", 0, hash160(secp_pub(path))))

# Cosmos, XRP, Filecoin (secp256k1). Binance Chain and Tezos are not in this firmware.
path = "m/44'/118'/0'/0/0"
check(f"Cosmos {path}", ask(M.CosmosGetAddress(address_n=parse_path(path), hrp="cosmos")), bech32("cosmos", hash160(secp_pub(path))))
path = "m/44'/144'/0'/0/0"
check(f"XRP {path}", ask(M.RippleGetAddress(address_n=parse_path(path))), b58check(b"\x00", hash160(secp_pub(path)), base58.RIPPLE_ALPHABET))
path = "m/44'/461'/0'/0/0"
payload = hashlib.blake2b(secp_pub(path, False), digest_size=20).digest()
cs = hashlib.blake2b(b"\x01" + payload, digest_size=4).digest()
fil = "f1" + base64.b32encode(payload + cs).decode().lower().rstrip("=")
check(f"Filecoin {path}", ask(M.FilecoinGetAddress(address_n=parse_path(path))), fil)

# ed25519 networks
path = "m/44'/148'/0'"
pub = slip10_ed25519(path)
body = bytes([6 << 3]) + pub
check(f"Stellar {path}", ask(M.StellarGetAddress(address_n=parse_path(path))),
      base64.b32encode(body + struct.pack("<H", crc16_xmodem(body))).decode().rstrip("="))
path = "m/44'/637'/0'/0'/0'"
check(f"Aptos {path}", ask(M.AptosGetAddress(address_n=parse_path(path))),
      "0x" + hashlib.sha3_256(slip10_ed25519(path) + b"\x00").hexdigest())
path = "m/44'/784'/0'/0'/0'"
check(f"Sui {path}", ask(M.SuiGetAddress(address_n=parse_path(path))),
      "0x" + hashlib.blake2b(b"\x00" + slip10_ed25519(path), digest_size=32).hexdigest())
path = "m/44'/397'/0'"
check(f"Near {path}", ask(M.NearGetAddress(address_n=parse_path(path))), slip10_ed25519(path).hex())
path = "m/44'/283'/0'/0'/0'"
pub = slip10_ed25519(path)
check(f"Algorand {path}", ask(M.AlgorandGetAddress(address_n=parse_path(path))),
      base64.b32encode(pub + hashlib.new("sha512_256", pub).digest()[-4:]).decode().rstrip("="))

print(f"{sum(results)}/{len(results)} passed")
