# End-to-end test of the DIY wallet

Runs the `DIY_HAT=1` build on an ordinary Linux PC, sets up a wallet on its
screen with its keys, then uses it the way the OneKey app does and checks every
answer independently.

What stands in for the Pi hardware (test only, never part of the wallet):

- `screen-keys-shim.c`: a plain file is the OLED framebuffer, a FIFO is the
  joystick and keys.
- `rng-shim.c`: `/dev/urandom` stands in for `/dev/hwrng`, and a fake `/sys`
  says the active generator is Broadcom's. (On a real Pi the wallet reads the
  real generator; this only lets the program start here.)

The wallet is reached over its local UDP port, which is what `usb-relay.py`
forwards the USB traffic to, using OneKey's fork of trezorlib from `python/` in
this repository.

## Run it

```sh
cd diy/test
cc -O2 -shared -fPIC -o screen-keys-shim.so screen-keys-shim.c -ldl
cc -O2 -shared -fPIC -o rng-shim.so rng-shim.c -ldl
python3 -m venv venv && . venv/bin/activate
pip install -e ../../python embit eth-account pillow base58 rlp

# 1. Start the wallet (built with EMULATOR=1 DIY_HAT=1 DEBUG_LINK=0).
python ctl.py serve ../../legacy/firmware/onekey_emu.elf - /tmp/e2e/run &

# 2. Set it up on the device: press keys and look at the saved screens
#    (/tmp/e2e/run/sheet.png after each call). Create a wallet, write down the
#    words as they are shown, pass the word check, set a PIN.
python drive.py /tmp/e2e/run right right right
python drive.py /tmp/e2e/run down:2 right      # key:count

# 3. Use it like the app does, with the words you read off the screen.
python wallet_flow.py /tmp/e2e/run "word1 word2 ... word12"
```

`wallet_flow.py` asks the wallet for Bitcoin (legacy, nested and native SegWit,
Taproot, mainnet and testnet, two accounts), Ethereum, Tron and Solana
addresses and account keys, signs a SegWit and a Taproot testnet transaction,
an EIP-1559 Ethereum transaction and two messages, pressing the wallet's
confirm key for each, and checks all of it against keys derived from the words
with embit, eth-account and eth-keys.

`python ctl.py <dir> save <file>` keeps the wallet's storage; pass it as the
last argument of `serve` to start from the same wallet again.

To test the ARM64 build under qemu-user, build both shims with
`aarch64-linux-gnu-gcc` and set `E2E_QEMU=1` and
`E2E_SHIMS="/path/screen-keys-shim.so /path/rng-shim.so"`.
