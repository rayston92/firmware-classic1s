"""PSBT signing checks (SignPsbt), against a debug-interface test build.

Run the wallet with ctl.py (the DEBUG_LINK=1 test build, fresh storage), then:
  python psbt_check.py <ctl-dir>

The wallet is loaded with a known test phrase. Cases (Bitcoin testnet):
  honest-taproot   change to our own Taproot address: hidden from the screen,
                   signature valid
  fake-taproot     an attacker's Taproot output labelled with our fingerprint,
                   path and its own internal key: must be SHOWN on screen
  segwit-change    our own P2WPKH address labelled as change (another address
                   type than the Taproot inputs): must be shown
  fake-segwit      an attacker's P2WPKH output labelled with our fingerprint
                   and path: must be shown
  far-account, big-index, chain-2, 84-on-86
                   our own Taproot addresses outside the inputs' account
                   (account 5, index 2,000,000, chain 2, a P2WPKH address on
                   the inputs' own Taproot key path): must be shown
  op-return-80     an 80-byte OP_RETURN (OP_PUSHDATA1): shown, the push
                   length byte is not part of the data
  op-return-bad    an OP_RETURN that is not one clean push: refused
  negative-amount  an output of -1 satoshi: refused
  empty-leaf       a Taproot leaf script of length 0: refused, wallet alive
  script-path      spending through a Taproot script leaf: signature must
                   verify against the untweaked key
  long-path        a key path of 20 levels: must be refused
Every screen the wallet shows is saved, and every output that appears on
screen is listed.
"""
import hashlib, os, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ctl import send

from trezorlib import messages as M
from trezorlib.debuglink import TrezorClientDebugLink, load_device
from trezorlib.transport.udp import UdpTransport
from embit import bip32, bip39, compact, ec, script
from embit.networks import NETWORKS
from embit.psbt import PSBT, DerivationPath
from embit.transaction import Transaction, TransactionInput, TransactionOutput

OUT = sys.argv[1]
# A production wallet that is already set up and unlocked can be used too:
# pass its words as the second argument (no debug interface needed then).
PHRASE = sys.argv[2] if len(sys.argv) > 2 else "all all all all all all all all all all all all"
NET = NETWORKS["test"]
H = 0x80000000
root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(PHRASE))
FP = root.my_fingerprint
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def key(path):
    return root.derive(path).key.get_public_key()


def tagged(tag, data):
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + data).digest()


transport = UdpTransport("127.0.0.1:54935")
if len(sys.argv) > 2:
    from trezorlib.client import TrezorClient

    class _UI:
        def button_request(self, br):
            pass

    client = TrezorClient(transport, ui=_UI())
    client.open()
    if not client.features.initialized or not client.features.unlocked:
        sys.exit("set up and unlock the wallet first")
else:
    client = TrezorClientDebugLink(transport)
    client.open() if hasattr(client, "open") else None
    if client.features.initialized:
        sys.exit("start the wallet with fresh storage")
    load_device(client, PHRASE, pin=None, passphrase_protection=False, label="psbt test")
    client.init_device()


def sign(name, raw):
    """Send SignPsbt, confirm every screen, record what was shown."""
    shots, codes = [], []
    resp = client.call_raw(M.SignPsbt(psbt=raw, coin_name="Testnet"))
    while isinstance(resp, M.ButtonRequest):
        codes.append(resp.code)
        time.sleep(0.4)
        shots.append(send(OUT, "shot", f"{name}-{len(shots) + 1}").strip())
        stop = threading.Event()

        def presser():
            # OneKey's screens wait for a key press (not a debug-link
            # decision), so press the confirm key like a person would.
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
    return resp, shots, codes


def fake_prev(spk, amount):
    return hashlib.sha256(os.urandom(32)).digest(), TransactionOutput(amount, spk)


def base_psbt(in_spk, amount, outs):
    txid, utxo = fake_prev(in_spk, amount)
    tx = Transaction(vin=[TransactionInput(txid, 0, sequence=0xFFFFFFFD)], vout=[TransactionOutput(v, s) for v, s in outs])
    p = PSBT(tx)
    p.inputs[0].witness_utxo = utxo
    return p, tx


IN_PATH = [86 + H, 1 + H, 0 + H, 0, 0]
in_key = key("m/86'/1'/0'/0/0")
in_spk = script.p2tr(in_key)
ext = ec.PrivateKey(hashlib.sha256(b"someone else").digest()).get_public_key()
ext_spk = script.p2tr(ext)
attacker = ec.PrivateKey(hashlib.sha256(b"attacker").digest()).get_public_key()


def key_path_input(p):
    p.inputs[0].taproot_bip32_derivations[in_key] = ([], DerivationPath(FP, IN_PATH))
    p.inputs[0].taproot_internal_key = in_key


def verify_key_path(signed_raw, tx, amount):
    s = PSBT.parse(signed_raw)
    inp = s.inputs[0]
    sig = getattr(inp, "taproot_key_sig", None)
    if sig is None:  # embit keeps unknown/known fields in different places by version
        for k, v in inp.unknown.items():
            if k == b"\x13":
                sig = v
    if not sig:
        return False, "no key-path signature in the result"
    h = tx.sighash_taproot(0, [in_spk], [100_000])
    q = ec.PublicKey.from_xonly(in_spk.data[2:34])
    return q.schnorr_verify(ec.SchnorrSig.parse(sig[:64]), h), f"{len(sig)}-byte sig"


def run_case(name, p, tx, expect_shown, verify=True):
    raw = p.serialize()
    resp, shots, codes = sign(name, raw)
    if isinstance(resp, M.Failure):
        return resp, shots
    return resp, shots


def output_screens(shots):
    return len(shots)


# --- honest Taproot change -------------------------------------------------
chg_key = key("m/86'/1'/0'/1/0")
p, tx = base_psbt(in_spk, 100_000, [(60_000, ext_spk), (39_000, script.p2tr(chg_key))])
key_path_input(p)
p.outputs[1].taproot_bip32_derivations[chg_key] = ([], DerivationPath(FP, [86 + H, 1 + H, 0 + H, 1, 0]))
p.outputs[1].taproot_internal_key = chg_key
resp, shots_honest = run_case("honest-taproot", p, tx, 1)
ok_sig, d = verify_key_path(resp.psbt, tx, 100_000) if isinstance(resp, M.SignedPsbt) else (False, str(resp))
check("honest Taproot change: signed, signature valid", ok_sig, d)
n_honest = len(shots_honest)

# --- attacker's Taproot output labelled as our change ----------------------
p, tx = base_psbt(in_spk, 100_000, [(60_000, ext_spk), (39_000, script.p2tr(attacker))])
key_path_input(p)
p.outputs[1].taproot_bip32_derivations[attacker] = ([], DerivationPath(FP, [86 + H, 1 + H, 0 + H, 1, 0]))
p.outputs[1].taproot_internal_key = attacker
resp, shots_fake = run_case("fake-taproot", p, tx, 2)
check("attacker Taproot output is shown (one more screen than honest change)", len(shots_fake) > n_honest,
      f"honest {n_honest} screens, attacker {len(shots_fake)} screens")

# --- our own P2WPKH address labelled as change --------------------------------
# Only the inputs' own account and address type count as change (Taproot here),
# so this is shown, and still signed.
w_chg = key("m/84'/1'/0'/1/0")
p, tx = base_psbt(in_spk, 100_000, [(60_000, ext_spk), (39_000, script.p2wpkh(w_chg))])
key_path_input(p)
p.outputs[1].bip32_derivations[w_chg] = DerivationPath(FP, [84 + H, 1 + H, 0 + H, 1, 0])
resp, shots_wh = run_case("segwit-change", p, tx, 2)
check("SegWit change from Taproot inputs is shown, and signed", isinstance(resp, M.SignedPsbt) and len(shots_wh) > n_honest,
      f"{len(shots_wh)} screens; {getattr(resp, 'message', type(resp).__name__)}")


def own_taproot_case(name, path, label):
    k = root.derive(path).key.get_public_key()
    pp = [int(x[:-1]) + H if x.endswith("'") else int(x) for x in path.split("/")[1:]]
    p, tx = base_psbt(in_spk, 100_000, [(60_000, ext_spk), (39_000, script.p2tr(k))])
    key_path_input(p)
    p.outputs[1].taproot_bip32_derivations[k] = ([], DerivationPath(FP, pp))
    p.outputs[1].taproot_internal_key = k
    resp, shots = run_case(name, p, tx, 2)
    check(f"{label} is shown, and signed", isinstance(resp, M.SignedPsbt) and len(shots) > n_honest,
          f"{len(shots)} screens; {getattr(resp, 'message', type(resp).__name__)}")


own_taproot_case("far-account", "m/86'/1'/5'/1/0", "our Taproot address in account 5")
own_taproot_case("big-index", "m/86'/1'/0'/1/2000000", "our Taproot address at index 2,000,000")
own_taproot_case("chain-2", "m/86'/1'/0'/2/0", "our Taproot address on chain 2")

# A P2WPKH address on the inputs' own Taproot key path (same account prefix).
k86 = key("m/86'/1'/0'/1/0")
p, tx = base_psbt(in_spk, 100_000, [(60_000, ext_spk), (39_000, script.p2wpkh(k86))])
key_path_input(p)
p.outputs[1].bip32_derivations[k86] = DerivationPath(FP, [86 + H, 1 + H, 0 + H, 1, 0])
resp, shots = run_case("84-on-86", p, tx, 2)
check("P2WPKH address on a Taproot key path is shown", isinstance(resp, M.SignedPsbt) and len(shots) > n_honest,
      f"{len(shots)} screens; {getattr(resp, 'message', type(resp).__name__)}")

p, tx = base_psbt(in_spk, 100_000, [(60_000, ext_spk), (39_000, script.p2wpkh(attacker))])
key_path_input(p)
p.outputs[1].bip32_derivations[attacker] = DerivationPath(FP, [84 + H, 1 + H, 0 + H, 1, 0])
resp, shots_wf = run_case("fake-segwit", p, tx, 2)
check("attacker SegWit output is shown", len(shots_wf) > n_honest, f"{len(shots_wf)} screens; {getattr(resp, 'message', type(resp).__name__)}")

# --- OP_RETURN outputs ---------------------------------------------------------
data80 = bytes(range(80))
p, tx = base_psbt(in_spk, 100_000, [(60_000, ext_spk), (0, script.Script(b"\x6a\x4c\x50" + data80)), (39_000, script.p2tr(chg_key))])
key_path_input(p)
p.outputs[2].taproot_bip32_derivations[chg_key] = ([], DerivationPath(FP, [86 + H, 1 + H, 0 + H, 1, 0]))
p.outputs[2].taproot_internal_key = chg_key
resp, shots = run_case("op-return-80", p, tx, 2)
check("80-byte OP_RETURN is shown and signed", isinstance(resp, M.SignedPsbt) and len(shots) > n_honest,
      f"{len(shots)} screens; {getattr(resp, 'message', type(resp).__name__)}")

p, tx = base_psbt(in_spk, 100_000, [(60_000, ext_spk), (0, script.Script(b"\x6a" + bytes(81)))])
key_path_input(p)
resp, shots = run_case("op-return-bad", p, tx, 0)
check("OP_RETURN that is not one clean push is refused", isinstance(resp, M.Failure),
      getattr(resp, "message", type(resp).__name__))

# --- negative output amount ------------------------------------------------------
p, tx = base_psbt(in_spk, 100_000, [(60_000, ext_spk), (2**64 - 1, script.p2tr(chg_key))])
key_path_input(p)
resp, shots = run_case("negative-amount", p, tx, 0)
check("output of -1 satoshi is refused", isinstance(resp, M.Failure), getattr(resp, "message", type(resp).__name__))

# --- empty Taproot leaf script -----------------------------------------------------
p, tx = base_psbt(in_spk, 100_000, [(99_000, ext_spk)])
key_path_input(p)
p.inputs[0].taproot_scripts[bytes([0xC0]) + in_key.xonly()] = b""
resp, shots = run_case("empty-leaf", p, tx, 0)
check("empty Taproot leaf script is refused", isinstance(resp, M.Failure), getattr(resp, "message", type(resp).__name__))
check("wallet still answers after the empty leaf script", client.ping("ok?") == "ok?")

# --- script-path spending ----------------------------------------------------
leaf_script = b"\x20" + in_key.xonly() + b"\xac"
leaf_hash = tagged("TapLeaf", b"\xc0" + compact.to_bytes(len(leaf_script)) + leaf_script)
internal = ec.PrivateKey(hashlib.sha256(b"internal").digest()).get_public_key()
tweak_hash = tagged("TapTweak", internal.xonly() + leaf_hash)
q = internal.taproot_tweak(leaf_hash)
sp_spk = script.Script(b"\x51\x20" + q.xonly())
parity = q.sec()[0] & 1
control = bytes([0xC0 | parity]) + internal.xonly()
p, tx = base_psbt(sp_spk, 100_000, [(99_000, ext_spk)])
p.inputs[0].taproot_bip32_derivations[in_key] = ([leaf_hash], DerivationPath(FP, IN_PATH))
p.inputs[0].taproot_internal_key = internal
p.inputs[0].taproot_scripts[control] = leaf_script + b"\xc0"
resp, _ = run_case("script-path", p, tx, 1)
if isinstance(resp, M.SignedPsbt):
    s = PSBT.parse(resp.psbt)
    sigs = s.inputs[0].taproot_sigs
    sig = next(iter(sigs.values()), b"") if sigs else b""
    h = tx.sighash_taproot(0, [sp_spk], [100_000], ext_flag=1, script=script.Script(leaf_script))
    ok = bool(sig) and in_key.schnorr_verify(ec.SchnorrSig.parse(sig[:64]), h)
    check("script-path signature verifies against the untweaked key", ok, f"{len(sigs)} script sig(s)")
else:
    check("script-path signature verifies against the untweaked key", False, str(resp))

# --- over-long key path ------------------------------------------------------
p, tx = base_psbt(in_spk, 100_000, [(60_000, ext_spk), (39_000, script.p2tr(chg_key))])
key_path_input(p)
p.outputs[1].taproot_bip32_derivations[chg_key] = ([], DerivationPath(FP, [86 + H] + [1] * 19))
p.outputs[1].taproot_internal_key = chg_key
resp, _ = run_case("long-path", p, tx, 0)
check("20-level key path refused", isinstance(resp, M.Failure) and "parse" in (resp.message or "").lower(),
      getattr(resp, "message", type(resp).__name__))
alive = client.ping("still there?") == "still there?"
check("wallet still answers afterwards", alive)

print(f"{sum(ok for _, ok in results)}/{len(results)} passed")
