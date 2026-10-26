"""What the screen shows vs what is signed, for the fixes in Ethereum, Tron,
Solana and message signing. Against a debug-interface test build with fresh
storage, run with ctl.py (see runcheck in the README):
  python display_check.py <ctl-dir>

Every screen at every confirmation is saved as <case>-N.png in the ctl dir.
"""
import hashlib, os, struct, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ctl import send

import base58
import rlp
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from eth_keys import keys as ethkeys
from eth_utils import keccak
from trezorlib import messages as M
from trezorlib.debuglink import TrezorClientDebugLink, load_device
from trezorlib.tools import parse_path
from trezorlib.transport.udp import UdpTransport

OUT = sys.argv[1]
PHRASE = "all all all all all all all all all all all all"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


client = TrezorClientDebugLink(UdpTransport("127.0.0.1:54935"))
client.open()
if client.features.initialized:
    sys.exit("start the wallet with fresh storage")
load_device(client, PHRASE, pin=None, passphrase_protection=False, label="display test")
client.init_device()


def call(name, msg, acks=None):
    """Send msg; at every ButtonRequest save the screen and press confirm.
    acks: optional function(resp) -> next message for multi-step requests."""
    shots = []
    resp = client.call_raw(msg)
    while True:
        if isinstance(resp, M.ButtonRequest):
            time.sleep(0.4)
            shots.append(send(OUT, "shot", f"{name}-{len(shots) + 1}").strip())
            stop = threading.Event()

            def presser():
                n = 0
                while not stop.is_set():
                    time.sleep(0.8)
                    if not stop.is_set():
                        n += 1  # save each screen before pressing confirm on it
                        send(OUT, "shot", f"{name}-{len(shots)}-{n}")
                        send(OUT, "right")

            client._raw_write(M.ButtonAck())
            th = threading.Thread(target=presser, daemon=True)
            th.start()
            resp = client._raw_read()
            stop.set()
            th.join()
            continue
        if acks:
            nxt = acks(resp)
            if nxt is not None:
                resp = client.call_raw(nxt)
                continue
        return resp, shots


def msg_of(r):
    return getattr(r, "message", None) or type(r).__name__


# ---------------- Ethereum ----------------
eth_path = parse_path("m/44h/60h/0h/0/0")
eth_addr = client.call(M.EthereumGetAddress(address_n=eth_path)).address


def eth_1559(name, data, first_chunk_len=None):
    first = data[:1024] if first_chunk_len is None else data[:first_chunk_len]
    rest = [data[len(first):]]

    def acks(resp):
        if isinstance(resp, M.EthereumTxRequest) and resp.data_length:
            chunk = rest[0][: resp.data_length]
            rest[0] = rest[0][resp.data_length:]
            return M.EthereumTxAck(data_chunk=chunk)
        return None

    m = M.EthereumSignTxEIP1559(
        address_n=eth_path, nonce=b"\x01", max_gas_fee=(30 * 10**9).to_bytes(5, "big"),
        max_priority_fee=(2 * 10**9).to_bytes(4, "big"), gas_limit=(200000).to_bytes(3, "big"),
        to="0x" + "5a" * 20, value=b"", data_initial_chunk=first, data_length=len(data), chain_id=1)
    return call(name, m, acks)


data = bytes(range(256)) * 6  # 1536 bytes
resp, shots = eth_1559("eth-1536", data)
if isinstance(resp, M.EthereumTxRequest) and resp.signature_r:
    fields = [1, 1, 2 * 10**9, 30 * 10**9, 200000, bytes.fromhex("5a" * 20), 0, data, []]
    digest = keccak(b"\x02" + rlp.encode(fields))
    sig = ethkeys.Signature(vrs=(resp.signature_v, int.from_bytes(resp.signature_r, "big"), int.from_bytes(resp.signature_s, "big")))
    signer = sig.recover_public_key_from_msg_hash(digest).to_checksum_address()
    check("ETH 1536-byte data: signed by the wallet's address", signer == eth_addr, f"{len(shots)} screens")
else:
    check("ETH 1536-byte data: signed by the wallet's address", False, msg_of(resp))

resp, _ = eth_1559("eth-short-chunk", bytes.fromhex("095ea7b3") + bytes(64), first_chunk_len=4)
check("ETH first chunk shorter than the data is refused", isinstance(resp, M.Failure), msg_of(resp))

# ---------------- Tron ----------------
trx_path = parse_path("m/44h/195h/0h/0/0")
trx_addr = client.call(M.TronGetAddress(address_n=trx_path)).address
contract = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # USDT on Tron


def tron(name, trigger):
    m = M.TronSignTx(address_n=trx_path, ref_block_bytes=b"\x12\x34", ref_block_hash=bytes(8),
                     expiration=1700000060000, timestamp=1700000000000, fee_limit=100_000_000,
                     contract=M.TronContract(trigger_smart_contract=trigger))
    return call(name, m)


def tron_signer(resp):
    h = hashlib.sha256(resp.serialized_tx).digest()
    s = resp.signature
    sig = ethkeys.Signature(vrs=(s[64] - 27, int.from_bytes(s[:32], "big"), int.from_bytes(s[32:64], "big")))
    pub = sig.recover_public_key_from_msg_hash(h)
    raw = b"\x41" + keccak(pub.to_bytes())[-20:]
    return base58.b58encode_check(raw).decode()


resp, shots = tron("tron-call-value", M.TronTriggerSmartContract(
    contract_address=contract, call_value=5_000_000, data=bytes.fromhex("12345678") + bytes(32)))
if isinstance(resp, M.TronSignedTx):
    has_value = b"\x18\xc0\x96\xb1\x02" in resp.serialized_tx  # field 3 = 5000000
    check("Tron call with 5 TRX: amount screen added, signed, value in the signed data",
          len(shots) >= 2 and has_value and tron_signer(resp) == trx_addr, f"{len(shots)} screens")
else:
    check("Tron call with 5 TRX: amount screen added, signed, value in the signed data", False, msg_of(resp))

unknown = base58.b58encode_check(b"\x41" + hashlib.sha256(b"unknown contract").digest()[:20]).decode()
data = bytes.fromhex("a9059cbb") + bytes(12) + hashlib.sha256(b"recipient").digest()[:20] + (10**6).to_bytes(32, "big")
resp, shots = tron("tron-unknown-token", M.TronTriggerSmartContract(contract_address=unknown, data=data))
check("Tron token-style call to an unknown contract: signed (screens saved; must name the contract)",
      isinstance(resp, M.TronSignedTx) and tron_signer(resp) == trx_addr, f"{unknown} {len(shots)} screens")

transfer = bytes.fromhex("a9059cbb") + bytes(12) + bytes(20) + (10**6).to_bytes(32, "big")
resp, _ = tron("tron-trc20-value", M.TronTriggerSmartContract(contract_address=contract, call_value=1, data=transfer))
check("Tron token transfer with TRX value is refused", isinstance(resp, M.Failure), msg_of(resp))
resp, _ = tron("tron-token-value", M.TronTriggerSmartContract(
    contract_address=contract, call_token_value=7, asset_id=1000001, data=bytes.fromhex("12345678")))
check("Tron call with a TRC-10 token value is refused", isinstance(resp, M.Failure), msg_of(resp))

# ---------------- Solana ----------------
sol_path = parse_path("m/44h/501h/0h/0h")
sol_addr = client.call(M.SolanaGetAddress(address_n=sol_path)).address
me = base58.b58decode(sol_addr)
STAKE = base58.b58decode("Stake11111111111111111111111111111111111111")
CLOCK = base58.b58decode("SysvarC1ock11111111111111111111111111111111")
HIST = base58.b58decode("SysvarStakeHistory1111111111111111111111111")
SYSTEM = bytes(32)
stake_acct = hashlib.sha256(b"stake account").digest()
dest = hashlib.sha256(b"withdraw destination").digest()
dest_b58 = base58.b58encode(dest).decode()


def shortvec(n):
    return bytes([n])  # n < 128 here


def sol_message(header, keys, instructions):
    out = bytes(header) + shortvec(len(keys)) + b"".join(keys) + hashlib.sha256(b"blockhash").digest()
    out += shortvec(len(instructions))
    for prog, accts, data in instructions:
        out += bytes([prog]) + shortvec(len(accts)) + bytes(accts) + shortvec(len(data)) + data
    return out


def sol_sign(name, message):
    resp, shots = call(name, M.SolanaSignTx(address_n=sol_path, raw_tx=message))
    ok = False
    if isinstance(resp, M.SolanaSignedTx):
        try:
            Ed25519PublicKey.from_public_bytes(me).verify(resp.signature, message)
            ok = True
        except Exception:
            pass
    return resp, shots, ok


transfer_msg = sol_message([1, 0, 1], [me, dest, SYSTEM], [(2, [0, 1], struct.pack("<IQ", 2, 1_500_000))])
resp, shots_tr, ok = sol_sign("sol-transfer", transfer_msg)
check("Solana transfer: signed, signature valid", ok, f"{len(shots_tr)} screens; {msg_of(resp)}")

withdraw_msg = sol_message([1, 0, 3], [me, stake_acct, dest, CLOCK, HIST, STAKE],
                           [(5, [1, 2, 3, 4, 0], struct.pack("<IQ", 4, 2_000_000_000))])
resp, shots_w, ok = sol_sign("sol-stake-withdraw", withdraw_msg)
check("Solana stake withdraw: signed, signature valid", ok, f"{len(shots_w)} screens; {msg_of(resp)}")
print("stake withdraw screens:", " ".join(os.path.basename(s) for s in shots_w), flush=True)

# ---------------- Message with a zero byte ----------------
text = "é\x00 send everything to the attacker".encode()
resp, shots_m = call("eth-msg-nul", M.EthereumSignMessage(address_n=eth_path, message=text))
if isinstance(resp, M.EthereumMessageSignature):
    prefix = b"\x19Ethereum Signed Message:\n" + str(len(text)).encode()
    s = resp.signature
    sig = ethkeys.Signature(vrs=(s[64] - 27, int.from_bytes(s[:32], "big"), int.from_bytes(s[32:64], "big")))
    signer = sig.recover_public_key_from_msg_hash(keccak(prefix + text)).to_checksum_address()
    check("ETH message with a zero byte: signed by the wallet (screen saved)", signer == eth_addr,
          " ".join(os.path.basename(x) for x in shots_m))
else:
    check("ETH message with a zero byte: signed by the wallet (screen saved)", False, msg_of(resp))

check("wallet still answers", client.ping("ok?") == "ok?")
print(f"{sum(results)}/{len(results)} passed")
