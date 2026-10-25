"""Use the running wallet the way the OneKey app does, and check every answer
independently from the 12 words shown on the wallet's screen.

  python wallet_flow.py <ctl-outdir> "<12 words>"

The wallet is reached over its local UDP port (what the USB relay forwards to)
with OneKey's fork of trezorlib. Confirmations on the wallet are made by
pressing its confirm key through the controller; every screen is saved.
Independent checks: embit (BIP32/BIP39, addresses, BIP143/BIP341 sighashes,
Schnorr), eth-account / eth-keys (Ethereum addresses, transaction and message
signatures).
"""
import hashlib, json, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ctl import send

from trezorlib import btc, ethereum, solana, tron, messages as M
from trezorlib import _ed25519
import base58, hmac
from trezorlib.client import TrezorClient
from trezorlib.tools import parse_path, H_
from trezorlib.transport.udp import UdpTransport

from embit import bip32, bip39, ec, script
from embit.networks import NETWORKS
from embit.transaction import Transaction, TransactionInput, TransactionOutput
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_keys import keys as ethkeys
import rlp
from eth_utils import keccak, to_checksum_address

OUT, WORDS = sys.argv[1], sys.argv[2]
Account.enable_unaudited_hdwallet_features()
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


class Presser:
    """While a call needs confirming on the wallet, look at the screen and press confirm."""

    def __init__(self):
        self.active = threading.Event()
        self.stop_all = False
        self.shots = []
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while not self.stop_all:
            if self.active.wait(0.2):
                time.sleep(1.0)
                if not self.active.is_set():
                    continue
                self.shots.append(send(OUT, "shot", "confirm").strip())
                if self.active.is_set():
                    send(OUT, "right")


class UI:
    def __init__(self, presser):
        self.p = presser
        self.requests = []

    def button_request(self, br):
        self.requests.append(br.code.name if hasattr(br.code, "name") else str(br.code))
        self.p.active.set()

    def get_pin(self, code=None):
        raise RuntimeError("wallet asked for a PIN from the computer")

    def get_passphrase(self, available_on_device=False):
        raise RuntimeError("wallet asked for a passphrase")


presser = Presser()
ui = UI(presser)


def confirmed(fn, *a, **kw):
    ui.requests.clear()
    try:
        return fn(*a, **kw)
    finally:
        presser.active.clear()
        time.sleep(0.3)


# ---------- independent derivation from the words on the screen ----------
seed = bip39.mnemonic_to_seed(WORDS)
root = bip32.HDKey.from_seed(seed)


def key(path):
    return root.derive(path)


def btc_addr(path, kind, net="main"):
    pub = key(path).key
    n = NETWORKS[net]
    if kind == "p2pkh":
        return script.p2pkh(pub).address(n)
    if kind == "p2sh-p2wpkh":
        return script.p2sh(script.p2wpkh(pub)).address(n)
    if kind == "p2wpkh":
        return script.p2wpkh(pub).address(n)
    if kind == "p2tr":
        return script.p2tr(pub).address(n)


transport = UdpTransport("127.0.0.1:54935")
transport.open()
client = TrezorClient(transport, ui=ui)
f = client.features
check("wallet reports initialized", f.initialized, f"vendor={f.vendor} onekey={getattr(f,'onekey_version',None)}")
check("wallet reports backup done", not f.needs_backup)
check("wallet has a PIN", f.pin_protection)

# ---------- 1. addresses ----------
ST = M.InputScriptType
cases = [
    ("BTC legacy", "Bitcoin", "m/44h/0h/0h/0/0", ST.SPENDADDRESS, "p2pkh", "main"),
    ("BTC nested segwit", "Bitcoin", "m/49h/0h/0h/0/0", ST.SPENDP2SHWITNESS, "p2sh-p2wpkh", "main"),
    ("BTC native segwit", "Bitcoin", "m/84h/0h/0h/0/0", ST.SPENDWITNESS, "p2wpkh", "main"),
    ("BTC native segwit #2", "Bitcoin", "m/84h/0h/0h/0/1", ST.SPENDWITNESS, "p2wpkh", "main"),
    ("BTC native segwit account 2", "Bitcoin", "m/84h/0h/1h/0/0", ST.SPENDWITNESS, "p2wpkh", "main"),
    ("BTC taproot", "Bitcoin", "m/86h/0h/0h/0/0", ST.SPENDTAPROOT, "p2tr", "main"),
    ("BTC testnet segwit", "Testnet", "m/84h/1h/0h/0/0", ST.SPENDWITNESS, "p2wpkh", "test"),
    ("BTC testnet taproot", "Testnet", "m/86h/1h/0h/0/0", ST.SPENDTAPROOT, "p2tr", "test"),
]
addresses = {}
for name, coin, path, st, kind, net in cases:
    dev = btc.get_address(client, coin, parse_path(path), script_type=st)
    ref = btc_addr(path.replace("h", "'"), kind, net)
    addresses[name] = dev
    check(f"address {name} {path}", dev == ref, dev)

# Showing an address on the wallet's screen (what "verify address" in the app does)
dev = confirmed(btc.get_address, client, "Bitcoin", parse_path("m/84h/0h/0h/0/0"), show_display=True, script_type=ST.SPENDWITNESS)
check("address shown on wallet and confirmed", dev == addresses["BTC native segwit"], f"button requests {ui.requests}")

# Account public keys (what the app uses to find balances)
for path, coin, st in [("m/84h/0h/0h", "Bitcoin", ST.SPENDWITNESS), ("m/86h/0h/0h", "Bitcoin", ST.SPENDTAPROOT), ("m/44h/0h/0h", "Bitcoin", ST.SPENDADDRESS)]:
    node = btc.get_public_node(client, parse_path(path), coin_name=coin, script_type=st).node
    ref = key(path.replace("h", "'"))
    check(f"account key {path}", node.public_key == ref.key.sec() and node.chain_code == ref.chain_code)

eth_paths = ["m/44h/60h/0h/0/0", "m/44h/60h/0h/0/1", "m/44h/60h/1h/0/0"]
eth_addr = {}
for path in eth_paths:
    dev = ethereum.get_address(client, parse_path(path))
    ref = Account.from_key(key(path.replace("h", "'")).key.secret).address
    ref2 = Account.from_mnemonic(WORDS, account_path=path.replace("h", "'"))
    eth_addr[path] = dev
    check(f"address ETH {path}", dev.lower() == ref.lower() and (ref2 is None or ref2.address.lower() == ref.lower()), dev)

# Two more coins on the other code paths: Tron (secp256k1, Ethereum-style
# hashing) and Solana (ed25519, SLIP-10).
def slip10_ed25519(seed, path):
    I = hmac.new(b"ed25519 seed", seed, hashlib.sha512).digest()
    k, c = I[:32], I[32:]
    for i in path:
        I = hmac.new(c, b"\x00" + k + i.to_bytes(4, "big"), hashlib.sha512).digest()
        k, c = I[:32], I[32:]
    return k

sol_path = "m/44h/501h/0h/0h"
dev = solana.get_address(client, parse_path(sol_path))
dev = getattr(dev, "address", dev)
ref = base58.b58encode(_ed25519.publickey_unsafe(slip10_ed25519(seed, parse_path(sol_path)))).decode()
check(f"address SOL {sol_path}", dev == ref, dev)

trx_path = "m/44h/195h/0h/0/0"
dev = tron.get_address(client, parse_path(trx_path))
dev = getattr(dev, "address", dev)
pub64 = ethkeys.PrivateKey(key(trx_path.replace("h", "'")).key.secret).public_key.to_bytes()
ref = base58.b58encode_check(b"\x41" + keccak(pub64)[12:]).decode()
check(f"address TRX {trx_path}", dev == ref, dev)

# ---------- 2. Bitcoin transactions (testnet), segwit and taproot ----------
def prev_tx_paying(spk, amount):
    t = Transaction(vin=[TransactionInput(hashlib.sha256(os.urandom(32)).digest(), 0)], vout=[TransactionOutput(amount, spk)])
    ref = M.TransactionType(
        version=t.version, lock_time=t.locktime,
        inputs=[M.TxInputType(prev_hash=t.vin[0].txid, prev_index=0, script_sig=b"", sequence=0xFFFFFFFF)],
        bin_outputs=[M.TxOutputBinType(amount=amount, script_pubkey=spk.data)],
    )
    return t.txid(), ref


ext_key = ec.PrivateKey(hashlib.sha256(b"someone else").digest())
ext_addr = script.p2wpkh(ext_key.get_public_key()).address(NETWORKS["test"])

for label, purpose, in_type, out_type, kind in [
    ("segwit", 84, ST.SPENDWITNESS, M.OutputScriptType.PAYTOWITNESS, "p2wpkh"),
    ("taproot", 86, ST.SPENDTAPROOT, M.OutputScriptType.PAYTOTAPROOT, "p2tr"),
]:
    in_path = f"m/{purpose}'/1'/0'/0/0"
    chg_path = f"m/{purpose}'/1'/0'/1/0"
    in_pub = key(in_path).key
    in_spk = script.p2wpkh(in_pub) if kind == "p2wpkh" else script.p2tr(in_pub)
    amount = 100_000
    txid, prev = prev_tx_paying(in_spk, amount)
    inputs = [M.TxInputType(address_n=parse_path(in_path), prev_hash=txid, prev_index=0, amount=amount, script_type=in_type, sequence=0xFFFFFFFD)]
    outputs = [
        M.TxOutputType(address=ext_addr, amount=60_000, script_type=M.OutputScriptType.PAYTOADDRESS),
        M.TxOutputType(address_n=parse_path(chg_path), amount=39_000, script_type=out_type),
    ]
    sigs, raw = confirmed(btc.sign_tx, client, "Testnet", inputs, outputs, prev_txes={txid: prev})
    tx = Transaction.parse(raw)
    paid = [(o.value, o.script_pubkey.address(NETWORKS["test"])) for o in tx.vout]
    chg_ref = btc_addr(chg_path, kind, "test")
    check(f"BTC {label} tx outputs as asked", paid == [(60_000, ext_addr), (39_000, chg_ref)], f"button requests {len(ui.requests)}")
    w = tx.vin[0].witness
    if kind == "p2wpkh":
        sig_der, pub = w.items[0], w.items[1]
        h = tx.sighash_segwit(0, script.p2pkh(in_pub), amount)
        ok = pub == in_pub.sec() and sig_der[-1] == 1 and in_pub.verify(ec.Signature.parse(sig_der[:-1]), h)
    else:
        sig = w.items[0]
        h = tx.sighash_taproot(0, [in_spk], [amount])
        out_key = ec.PublicKey.from_xonly(in_spk.data[2:34])
        ok = len(sig) == 64 and out_key.schnorr_verify(ec.SchnorrSig.parse(sig), h)
    check(f"BTC {label} signature valid (checked by embit)", ok, raw.hex()[:32] + "...")

# ---------- 3. Ethereum transaction (EIP-1559) ----------
path = "m/44h/60h/0h/0/0"
to = "0x" + "5a" * 20
tx = dict(nonce=7, gas_limit=21000, to=to, value=10**16, chain_id=1, max_gas_fee=30 * 10**9, max_priority_fee=2 * 10**9)
v, r, s = confirmed(ethereum.sign_tx_eip1559, client, parse_path(path), **tx)
fields = [tx["chain_id"], tx["nonce"], tx["max_priority_fee"], tx["max_gas_fee"], tx["gas_limit"], bytes.fromhex(to[2:]), tx["value"], b"", []]
digest = keccak(b"\x02" + rlp.encode(fields))
sig = ethkeys.Signature(vrs=(v, int.from_bytes(r, "big"), int.from_bytes(s, "big")))
signer = sig.recover_public_key_from_msg_hash(digest).to_checksum_address()
check("ETH EIP-1559 tx signed by the wallet's address", signer.lower() == eth_addr[path].lower(), f"v={v} button requests {len(ui.requests)}")
signed_raw = b"\x02" + rlp.encode(fields + [v, int.from_bytes(r, "big"), int.from_bytes(s, "big")])
check("ETH raw tx decodes back to the same sender (eth-account)", Account.recover_transaction(signed_raw).lower() == eth_addr[path].lower())

# ---------- 4. Messages ----------
msg = "diywallet.org test message"
res = confirmed(ethereum.sign_message, client, parse_path(path), msg)
rec = Account.recover_message(encode_defunct(text=msg), signature=res.signature)
check("ETH personal_sign recovers to the wallet's address", rec.lower() == eth_addr[path].lower() and res.address.lower() == rec.lower())

res = confirmed(btc.sign_message, client, "Bitcoin", parse_path("m/84h/0h/0h/0/0"), msg, script_type=ST.SPENDWITNESS)
sigb = res.signature
data = b"\x18Bitcoin Signed Message:\n" + bytes([len(msg)]) + msg.encode()
h = hashlib.sha256(hashlib.sha256(data).digest()).digest()
recid = (sigb[0] - 27) & 3
pub = ethkeys.Signature(vrs=(recid, int.from_bytes(sigb[1:33], "big"), int.from_bytes(sigb[33:65], "big"))).recover_public_key_from_msg_hash(h)
comp = (b"\x03" if pub.to_bytes()[-1] & 1 else b"\x02") + pub.to_bytes()[:32]
check("BTC signed message recovers to the address's key", comp == key("m/84'/0'/0'/0/0").key.sec() and res.address == addresses["BTC native segwit"], f"header={sigb[0]}")

presser.stop_all = True
send(OUT, "shot", "end")
print(json.dumps({"passed": sum(ok for _, ok, _ in results), "total": len(results)}))
