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

## Other checks in this folder

| Script | What it checks |
|---|---|
| `entropy_check.py` | The recovery phrase is exactly what `rand_hw.c` should produce from known hardware and kernel random data (both replaced and logged by `rng-shim.so`) |
| `storage_check.py` | The storage file holds the phrase only encrypted, readable with the right PIN on the same board |
| `psbt_check.py` | PSBT change detection, key-path bounds, Taproot key-path and script-path signatures |
| `nostr_check.py` | Nostr key, event and Schnorr signatures, NIP-04 encryption both ways |
| `message_sweep.py` | Sends every message type once with minimal contents and watches for crashes and freezes |
| `display_check.py` | What the screen shows against what is signed: long Ethereum data, a short first data chunk, a Tron contract call carrying TRX, Solana transfer and stake withdrawal, a message with a hidden zero byte |
| `addresses_check.py` | Addresses for Litecoin, Dogecoin, Cosmos, XRP, Filecoin, Stellar, Aptos, Sui, Near and Algorand against independent derivation |
| `hwrng_check_test.py` | `hwrng-check` against a simulated Pi whose `/sys` and `/proc/iomem` look like a current Raspberry Pi OS kernel's (and an older one's): it accepts Broadcom's setting and refuses a changed setting, a silent generator, another driver, a look-alike name and an address that doesn't match the name; its statistical test passes good data and fails a stuck source |
| `rng_failures.py` | The production wallet started with a broken random number source (missing, replaced by `/dev/urandom` or a file, stuck, biased, failing after the warm-up, another driver, the kernel's generator failing): it must stop with "Random number generator failed" every time, and start normally with a working one |
| `passphrase_check.py` | 12-, 18- and 24-word phrases with empty, plain, 50-byte and non-English passphrases give the same Bitcoin and Ethereum addresses as independent derivation; a 51-byte passphrase is refused, not cut short |
| `autolock_check.py` | Auto-lock from the home screen and the menus, an open prompt staying usable, the delay set from the app (production build, about 7 minutes) |

`pi-os/` rehearses the guide's steps 3 to 5 on the real Raspberry Pi OS image
(its kernel, systemd and udev) on an emulated Pi, including USB through a
software USB controller; see `pi-os/README.md` for what it checks, what it
cannot show, and the three bugs it found.

`run-check.sh` starts a wallet, runs one of these against it and stops it, in
its own network namespace. `unlock_then.py` enters the PIN on the wallet's
screen (`pin_entry.py` reads the digit wheel from the screenshots) and then
runs a check, for production builds started from a saved storage file.

`psbt_check.py`, `nostr_check.py`, `display_check.py`, `addresses_check.py` and `passphrase_check.py` load a test phrase through the debug
interface, so they need a test build (`DEBUG_LINK=1
DIY_TEST_BUILD_WITH_DEBUG_LINK=1`, never for real use). Given the words of a
production wallet that is already set up and unlocked as a second argument,
they run against it instead.
