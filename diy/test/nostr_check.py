"""Nostr checks against a debug-interface test build (fresh storage), run with ctl.py:
  python nostr_check.py <ctl-dir>

Loads a known test phrase, then checks the Nostr key, event signing, Schnorr
signing, and NIP-04 encryption both ways, all against an independent
implementation (embit for keys and BIP-340, cryptography for AES).
"""
import base64, hashlib, json, os, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ctl import send

from trezorlib import messages as M
from trezorlib.debuglink import TrezorClientDebugLink, load_device
from trezorlib.tools import parse_path
from trezorlib.transport.udp import UdpTransport
from embit import bip32, bip39, ec
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

OUT = sys.argv[1]
# A production wallet that is already set up and unlocked can be used too:
# pass its words as the second argument (no debug interface needed then).
PHRASE = sys.argv[2] if len(sys.argv) > 2 else "all all all all all all all all all all all all"
PATH = "m/44h/1237h/0h/0/0"
root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(PHRASE))
me = root.derive(PATH.replace("h", "'"))
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


t = UdpTransport("127.0.0.1:54935")
if len(sys.argv) > 2:
    from trezorlib.client import TrezorClient

    class _UI:
        def button_request(self, br):
            pass

    client = TrezorClient(t, ui=_UI())
    client.open()
    if not client.features.initialized or not client.features.unlocked:
        sys.exit("set up and unlock the wallet first")
else:
    client = TrezorClientDebugLink(t)
    client.open() if hasattr(client, "open") else None
    if client.features.initialized:
        sys.exit("start the wallet with fresh storage")
    load_device(client, PHRASE, pin=None, passphrase_protection=False, label="nostr test")
    client.init_device()


def call(msg):
    resp = client.call_raw(msg)
    while isinstance(resp, M.ButtonRequest):
        stop = threading.Event()

        def presser():
            while not stop.is_set():
                time.sleep(0.6)
                if not stop.is_set():
                    send(OUT, "right")

        client._raw_write(M.ButtonAck())
        th = threading.Thread(target=presser, daemon=True)
        th.start()
        resp = client._raw_read()
        stop.set()
        th.join()
    return resp


n = parse_path(PATH)
r = call(M.NostrGetPublicKey(address_n=n))
pk = me.key.xonly().hex()
check("Nostr public key", getattr(r, "publickey", None) == pk, getattr(r, "publickey", r))

event = {"kind": 1, "created_at": 1700000000, "tags": [], "content": "hello from diywallet"}
r = call(M.NostrSignEvent(address_n=n, event=json.dumps(event).encode()))
if isinstance(r, M.NostrSignedEvent):
    out = json.loads(r.event)
    ser = json.dumps([0, pk, event["created_at"], event["kind"], event["tags"], event["content"]], separators=(",", ":"))
    want_id = hashlib.sha256(ser.encode()).hexdigest()
    ok_sig = me.key.schnorr_verify(ec.SchnorrSig.parse(bytes.fromhex(out["sig"])), bytes.fromhex(want_id))
    check("event id is the NIP-01 hash", out["id"] == want_id, out["id"][:16])
    check("event signature verifies against the Nostr key", ok_sig, out["sig"][:16])
else:
    check("event signing", False, r)

h = hashlib.sha256(b"something to sign").digest()
r = call(M.NostrSignSchnorr(address_n=n, hash=h.hex()))
ok = isinstance(r, M.NostrSignedSchnorr) and me.key.schnorr_verify(ec.SchnorrSig.parse(r.signature), h)
check("Schnorr signature verifies", ok, getattr(r, "signature", r).hex()[:16] if isinstance(r, M.NostrSignedSchnorr) else r)

peer = ec.PrivateKey(hashlib.sha256(b"peer").digest())
peer_pk = peer.get_public_key().xonly().hex()


def shared_x(priv_bytes, xonly_hex):
    pub = ec.PublicKey.parse(b"\x02" + bytes.fromhex(xonly_hex))
    # ECDH: x coordinate of priv * pub
    from embit.util import secp256k1
    pt = secp256k1.ec_pubkey_parse(pub.sec())
    secp256k1.ec_pubkey_tweak_mul(pt, priv_bytes)
    return secp256k1.ec_pubkey_serialize(pt)[1:33]


msg_text = "meet at noon"
r = call(M.NostrEncryptMessage(address_n=n, pubkey=peer_pk, msg=msg_text))
if isinstance(r, M.NostrEncryptedMessage):
    ct_b64, iv_b64 = r.msg.split("?iv=")
    key = shared_x(peer.secret, pk)
    d = Cipher(algorithms.AES(key), modes.CBC(base64.b64decode(iv_b64))).decryptor()
    padded = d.update(base64.b64decode(ct_b64)) + d.finalize()
    u = padding.PKCS7(128).unpadder()
    plain = (u.update(padded) + u.finalize()).decode()
    check("NIP-04: wallet encrypts, peer decrypts", plain == msg_text, plain)
else:
    check("NIP-04: wallet encrypts, peer decrypts", False, r)

key = shared_x(peer.secret, pk)
iv = os.urandom(16)
p = padding.PKCS7(128).padder()
data = p.update(b"reply from peer") + p.finalize()
e = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
ct = base64.b64encode(e.update(data) + e.finalize()).decode() + "?iv=" + base64.b64encode(iv).decode()
r = call(M.NostrDecryptMessage(address_n=n, pubkey=peer_pk, msg=ct))
check("NIP-04: peer encrypts, wallet decrypts", getattr(r, "msg", None) == "reply from peer", getattr(r, "msg", r))


def peer_encrypt(plain_bytes, pad=True, iv=None):
    iv = iv or os.urandom(16)
    if pad:
        pd = padding.PKCS7(128).padder()
        plain_bytes = pd.update(plain_bytes) + pd.finalize()
    e = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(e.update(plain_bytes) + e.finalize()).decode() + "?iv=" + base64.b64encode(iv).decode()


def wallet_roundtrip(text):
    r = call(M.NostrEncryptMessage(address_n=n, pubkey=peer_pk, msg=text))
    if not isinstance(r, M.NostrEncryptedMessage):
        return False, r
    ct_b64, iv_b64 = r.msg.split("?iv=")
    d = Cipher(algorithms.AES(key), modes.CBC(base64.b64decode(iv_b64))).decryptor()
    padded = d.update(base64.b64decode(ct_b64)) + d.finalize()
    u = padding.PKCS7(128).unpadder()
    return (u.update(padded) + u.finalize()).decode() == text, len(r.msg)


# Message lengths at the padding and size edges.
for length in (16, 31, 32, 1135):
    ok, d = wallet_roundtrip("x" * length)
    check(f"NIP-04: wallet encrypts a {length}-character message, peer decrypts", ok, d)
for length in (16, 1135):
    r = call(M.NostrDecryptMessage(address_n=n, pubkey=peer_pk, msg=peer_encrypt(b"y" * length)))
    check(f"NIP-04: peer encrypts {length} characters, wallet decrypts", getattr(r, "msg", None) == "y" * length,
          len(getattr(r, "msg", "")) if hasattr(r, "msg") else r)

# Things the wallet must refuse instead of overrunning its buffers.
r = call(M.NostrEncryptMessage(address_n=n, pubkey=peer_pk, msg="z" * 1136))
check("message over the size limit is refused", isinstance(r, M.Failure), getattr(r, "message", r))
r = call(M.NostrEncryptMessage(address_n=n, pubkey=peer_pk + "00" * 13, msg="hi"))
check("over-long peer key is refused (encrypt)", isinstance(r, M.Failure), getattr(r, "message", r))
r = call(M.NostrDecryptMessage(address_n=n, pubkey=peer_pk + "00" * 13, msg=peer_encrypt(b"hi")))
check("over-long peer key is refused (decrypt)", isinstance(r, M.Failure), getattr(r, "message", r))
good = peer_encrypt(b"hello")
r = call(M.NostrDecryptMessage(address_n=n, pubkey=peer_pk, msg=good.split("?iv=")[0] + "?iv=" + "A" * 1400))
check("over-long iv is refused", isinstance(r, M.Failure), getattr(r, "message", r))
bad_pad = peer_encrypt(b"A" * 14 + b"\x00\x0f", pad=False)
r = call(M.NostrDecryptMessage(address_n=n, pubkey=peer_pk, msg=bad_pad))
check("bad padding is refused", isinstance(r, M.Failure), getattr(r, "message", r))
r = call(M.NostrEncryptMessage(address_n=n, pubkey=peer_pk[:62] + "ZZ", msg="hi"))
check("peer key with non-hex characters is refused", isinstance(r, M.Failure), getattr(r, "message", r))
r = call(M.NostrSignSchnorr(address_n=n, hash=h.hex()[:62] + "ZZ"))
check("Schnorr hash with non-hex characters is refused", isinstance(r, M.Failure), getattr(r, "message", r))
r = call(M.NostrSignSchnorr(address_n=n, hash=h.hex()[:62]))
check("short Schnorr hash is refused", isinstance(r, M.Failure), getattr(r, "message", r))
check("wallet still answers", client.ping("ok?") == "ok?")
print(f"{sum(results)}/{len(results)} passed")
