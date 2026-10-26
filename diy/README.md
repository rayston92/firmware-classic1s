# DIY build: OneKey Classic firmware on a Raspberry Pi

This folder turns the Classic 1S emulator into something you can build into a
small device: a Raspberry Pi Zero 2 W with a 1.3" OLED screen HAT, plugged into
your computer over USB.

Step-by-step instructions for non-developers are on the tutorial website. This
file is the short version and the reference for what was changed.

## What the DIY changes do

The stock emulator is a development tool and is not safe for real funds. The
DIY build changes these things:

| Stock emulator | DIY build (`DIY=1`) | Where |
|---|---|---|
| Random numbers come from a fixed formula starting at 0, so every emulator creates the **same recovery phrase** | Random numbers come from the Pi's Broadcom hardware generator (`/dev/hwrng`, 10:183 only; on the HAT build the driver must be `bcm2835-rng`). The first 0x40000 words (1 MiB) are discarded, Broadcom's warm-up count. NIST SP 800-90B start-up and continuous health tests on every byte, SHA-256 conditioning at 4x oversampling, then hashed with 32 bytes from `getrandom`. Any failure stops the wallet with "Wallet stopped" on the screen and the reason in the log; there is no fallback. | `legacy/emulator/diy/rand_hw.c`, `legacy/Makefile.include` |
| (not applicable) | Before every start, as root: `hwrng-check` confirms `/dev/hwrng` is the Broadcom block (driver and device tree) and reads its control register through `/dev/mem`. Only `RNG_CTRL = 0x00000001` is accepted (on, all ring oscillators, internal jitter clock, normal speed), the setting Broadcom's own driver code writes; with anything else the wallet does not start. The check only reads (the registers are mapped read-only) and never changes the generator. | `diy/hwrng-check.c`, `diy/diywallet.service.in` |
| Listens on every network interface | Listens on 127.0.0.1 only | `legacy/emulator/udp.c` |
| Storage encryption salt is all zeros | Salt derived from the Pi's serial number | `legacy/emulator/diy/hw_salt.c`, `legacy/firmware/trezor.c` |
| Screen size 256x128 on this branch | The real Classic size, 128x64 | `legacy/oled.h` |
| Memory can be swapped out and core-dumped | Memory locked in RAM (`mlockall`), no core dumps, not debuggable by other processes | `legacy/emulator/diy/harden.c`, `legacy/emulator/setup.c` |
| `-Werror` | Warnings are shown but don't stop the build (upstream CI only builds on x86) | `legacy/Makefile.include` |
| Changing build options reuses objects built with the old ones | The options are remembered in `legacy/.diy-flags`; when they change, everything is rebuilt and the libraries are recreated | `legacy/Makefile.include`, `legacy/Makefile`, `legacy/emulator/Makefile`, `legacy/firmware/protob/Makefile` |
| Software key derivation leaves the public key empty (the secure element fills it on the device): wrong Ethereum/Tron addresses, all-zero SegWit witness keys | Public key filled after derivation | `legacy/firmware/fsm.c`, `legacy/firmware/signing.c` |
| BIP-340 "internal" (untweaked) signing tweaks the key anyway; Nostr signing returns an unset buffer as the signature; Nostr encryption disabled | Untweaked signing and ECDH as on the device; Nostr works | `crypto/bip32.c`, `legacy/firmware/nostr.c` |
| Cardano: some requests answer with keys, others read an unset key and crash the wallet | Every Cardano request is refused | `legacy/firmware/messages.c` |
| Alephium: the transaction decoder does not follow Alephium's format exactly, so a transaction built by the computer could be shown with other outputs than the ones signed (also in 3.21) | Every Alephium request is refused (the decoder's memory-safety fixes from 3.21 are in anyway) | `legacy/firmware/messages.c` |
| Auto-lock locks and blanks the screen underneath a waiting prompt, which a key press can still confirm; delay 0 ("never") locks constantly; the app's auto-lock setting does nothing | Locks only from the home screen and the menus, as Trezor does, and wipes the cached seed; a prompt stays on screen until answered; key presses count as use; 0 means never; the app's setting applies | `legacy/firmware/layout2.c`, `legacy/firmware/protect.c`, `legacy/firmware/fsm_msg_common.h` |
| Requests for things the emulator doesn't have (raw secure-element commands, security-key credentials, firmware hash) hang the connection or crash the wallet | Refused with a message | `legacy/firmware/fsm_msg_common.h`, `legacy/firmware/fsm_msg_webauthn.h` |
| Screens show the emulator's placeholders for parts the Pi doesn't have: a secure-chip version (1.1.0.0), a Bluetooth name and version (K0000, 1.0.1) on Device Info and the home screen, a Bluetooth switch, OneKey's FCC and ANATEL marks, and an erase warning about the Secure Element. Opening Device Info crashes the wallet (it hashes a firmware image the emulator doesn't have) | Secure chip and Bluetooth shown as None or left out, Bluetooth switch and Certification screen removed, erase warning reworded in all eight languages; Device Info shows the version without the hash (any emulator build) | `legacy/firmware/layout2.c`, `legacy/firmware/menu_list.c` |
| A DIY build with `DEBUG_LINK=1` is possible | Refused by the build (`DIY_TEST_BUILD_WITH_DEBUG_LINK=1` for test builds only); `install.sh` installs only a program carrying the wallet build marker, which survives stripping | `legacy/Makefile.include`, `legacy/emulator/diy/harden.c`, `diy/install.sh` |

Fixes in code shared with the device firmware. Some come from OneKey's later
releases (up to 3.21.0, marked "from 3.21"); the others are not fixed there
either (marked "also in 3.21").

| Problem | DIY branch | Where |
|---|---|---|
| PSBT: an output counts as change, and is left off the screen, if the computer labels it with this wallet's master fingerprint (Taproot: and an internal key it also supplies) | Only if the key path really produces that output's address, in the inputs' own account and address type (purpose, coin, account; chain 0 or 1; index up to 1,000,000), as Trezor requires. 3.21 checks the address but accepts any key path, so a compromised computer could still hide part of the amount in an address the app never looks at | `legacy/firmware/fsm_msg_coin.h` |
| PSBT: a key path longer than 8 levels is written past its array; BIP32 paths are read from the rest of the PSBT; values are read through a buffer that no longer exists; a Taproot leaf script of length 0 copies far past the buffer; an OP_RETURN of 81 bytes overflows by one and shows its length byte as data; negative amounts are accepted (from 3.21) | Bounded, read in place from each entry's own value, refused | `legacy/firmware/psbt/psbt.c`, `legacy/firmware/fsm_msg_coin.h`, `legacy/util.c` |
| Ethereum: a first data chunk shorter than 1024 bytes shows zeros on screen where other bytes are signed (from 3.21, which fixed only one of the two copies); the data length shown is cut to 16 bits and read past the buffer (from 3.21, which then shows "1024 bytes" for longer data); OneKey-style signing survives Cancel and locking (from 3.21) | Refused; the full length is shown, with the first 1024 bytes and "..." when there is more; aborted | `legacy/firmware/ethereum.c`, `legacy/firmware/ethereum_onekey.c`, `legacy/firmware/layout2.c`, `legacy/firmware/fsm.c` |
| Signing screens keep waiting after the computer sends Cancel, and a later key press signs anyway (from 3.21) | The screen ends | `legacy/buttons.h`, `legacy/firmware/protect.h`, `legacy/firmware/layout2.c`, `legacy/firmware/oled_text.c` |
| Tron: contract data up to 2048 bytes is copied into a 1024-byte stack buffer (from 3.21); TRX sent along with a contract call is never shown, and a transfer call to an unknown token contract is shown as a transfer of "UNKN" tokens without naming the contract (also in 3.21); amounts over 11 characters show as empty (also in 3.21) | Buffer sized for the largest call; the TRX amount is shown; an unknown token is shown as a contract call with its address and data (TRX or TRC-10 value with a token transfer refused); wider amount text | `legacy/firmware/tron.c` |
| Solana: the destination of a stake, nonce or vote withdrawal, a stake split or a token mint is never shown (also in 3.21); raw-message signing also signs off-chain messages (from 3.21) | Shown; refused | `legacy/firmware/solana.c`, `legacy/firmware/fsm_msg_solana.h` |
| Messages with a zero byte are shown as text up to that byte, and signed in full (also in 3.21) | Text with control bytes is shown as hex | `legacy/util.c`, `legacy/firmware/solana.c` |
| LNURL-auth signs even after the user rejects it (from 3.21) | Stops | `legacy/firmware/fsm_msg_lnurl.h` |
| Nostr encrypt/decrypt: no limits on message, IV, padding and peer key lengths, so the computer can overwrite memory (also in 3.21; reachable once Nostr works) | Checked, and keys and hashes must be real hex | `legacy/firmware/nostr.c` |
| Bitcoin fee page: its title is appended again each time the page is shown, overrunning its buffer after a few up/down presses; Nervos replies with leftover stack bytes in an unset field; Stellar sends two replies for an invalid memo type or a failed key derivation, and shows a dialog after an invalid asset (also in 3.21) | Fixed | `legacy/firmware/layout2.c`, `legacy/firmware/nervos.c`, `legacy/firmware/stellar.c`, `legacy/firmware/fsm_msg_stellar.h` |
| TON: the BOC parser reads and writes past its buffers and signs a different message than the one shown when the body is inlined; an extra destination crashes the wallet (from 3.21); a long memo or jetton comment overruns the cell buffer (also in 3.21) | Bounded parser that signs exactly what it shows; long memos refused | `legacy/firmware/ton.c`, `legacy/firmware/ton_cell.c`, `legacy/firmware/ton_bits.c` |
| Kaspa: replies after a rejection, and an unfinished session lets the next transaction's first input be signed without a prompt (from 3.21); test-network ECDSA addresses overflow their buffer (also in 3.21) | Fixed; such addresses are refused | `legacy/firmware/kaspa.c`, `legacy/firmware/fsm_msg_kaspa.h` |
| Stellar: an operation with an invalid asset issuer is still counted and signed (also in 3.21); double replies after a rejection (from 3.21) | Not signed; one reply | `legacy/firmware/stellar.c`, `legacy/firmware/fsm_msg_stellar.h` |
| Nervos: a finished session signs again; replies after a rejection (from 3.21). Neo: a translated title overruns its buffer (from 3.21). Kaspa, SCDO, Conflux, Nervos and Alephium sessions survive Cancel, Initialize and locking (from 3.21) | Fixed | `legacy/firmware/nervos.c`, `legacy/firmware/neo.c`, `legacy/firmware/fsm.c` |
| A Cancel from the computer turns safety checks off, because "no key" counted as yes (from 3.21); LockDevice leaves cached seeds, so the PIN alone reopens a passphrase wallet (from 3.21); one press on the amount page signs and skips the final page; the final page's text is garbled; dialog body text such as a non-standard path is never drawn; Change PIN leaves PIN copies in memory (from 3.21) | Only an explicit confirm counts; seeds cleared; final page shown and readable; text drawn; copies wiped | `legacy/firmware/layout2.c`, `legacy/firmware/fsm_msg_common.h`, `legacy/firmware/protect.c` |
| Bad Ethereum message-signature checks send two replies, which puts the app's next request out of step (also in 3.21) | One reply | `legacy/firmware/ethereum.c`, `legacy/firmware/ethereum_onekey.c` |

`DIY_HAT=1` additionally replaces the SDL window and keyboard with:

| | |
|---|---|
| `legacy/emulator/diy/oled_fb.c` | Draws on the SH1106 OLED through the kernel framebuffer (`dtoverlay=sh1106-spi`; the fbtft `fb_sh1106` or DRM `ssd130x` driver) |
| `legacy/emulator/diy/evdev_keys.c`, `buttons_evdev.c` | Reads the joystick and keys as Linux key events (`dtoverlay=gpio-key`). Each key line is its own input device and may appear after the wallet starts, so it keeps looking for new ones every 2 seconds |

Always build with `DEBUG_LINK=0`. The debug interface can read the recovery
phrase.

Files in this folder:

| File | Purpose |
|---|---|
| `usb-gadget.sh` | Gives the Pi's USB port the USB identity of a Classic 1S (1209:4F4B, manufacturer "DIY") |
| `usb-relay.py` | Passes 64-byte USB packets to and from the wallet's local UDP port, unchanged; keeps going while the wallet restarts |
| `diywallet.service.in`, `diywallet-usb.service` | systemd services; the wallet runs as its own user with only the screen, keys and hardware random number generator. Before it starts: swap off, text console detached from the framebuffers. No core dumps |
| `config.txt.snippet` | Screen, keys, USB device mode, Wi-Fi and Bluetooth off |
| `install.sh` | Installs all of the above; builds and runs `hwrng-check` and stops if it fails; lets only the wallet read the generator; turns swap off |
| `hwrng-check.c` | Root helper: checks, read-only, that the generator is the Pi's Broadcom block with Broadcom's register settings. `hwrng-check --test` also runs the FIPS 140-2 statistical tests on 1000 blocks of raw output |
| `rng-assess.sh` | Collects 1,000,000 raw bytes for a NIST SP 800-90B entropy assessment (`ea_non_iid`) |
| `set-home-icon.py` | Replaces the 24x24 home-screen icon in `legacy/gen/bitmaps.c` (the repo's own `generate.py` no longer reproduces that file) |
| `app-skip-genuine-check.patch` | For the OneKey app (tag v6.6.3): skips the genuine-device check, which a self-built device can never pass |

## Build and install on the Pi

Raspberry Pi OS Lite (64-bit), with the HAT fitted:

```sh
sudo apt install -y git build-essential python3-venv protobuf-compiler libprotobuf-dev
git clone --recurse-submodules --shallow-submodules --depth 1 -b diy <this repository> wallet
cd wallet
python3 -m venv ~/wallet-venv && . ~/wallet-venv/bin/activate
pip install "nanopb==0.4.*" protobuf mako munch click requests typing_extensions
cd legacy
EMULATOR=1 DIY_HAT=1 DEBUG_LINK=0 ./script/cibuild
cd ..
sudo ./diy/install.sh            # or --ram-only to forget the wallet at power-off
sudo reboot
```

Add `BITCOIN_ONLY=1` to the build line for a Bitcoin-only wallet. When you
change build options, clean first, because the makefiles don't notice changed
flags: `make -C emulator clean && make clean && make -C firmware clean`.

If the picture is upside down:

```sh
echo DIY_OLED_ROTATE=180 | sudo tee /etc/diywallet.conf
sudo systemctl restart diywallet
```

## Test on a desktop computer first

Same build without `DIY_HAT` gives a window and uses the arrow keys
(Left = back, Right = confirm):

```sh
cd legacy
EMULATOR=1 DIY=1 DEBUG_LINK=0 ./script/cibuild
./firmware/onekey_emu.elf
```

## What was checked, and what was not

Checked on x86-64 Linux (Ubuntu 24.04, gcc 13), unless noted:

- `DIY=1` and `DIY_HAT=1` both build with `DEBUG_LINK=0`; the HAT build does not link SDL.
- The `DIY_HAT=1` build also compiles with the ARM64 cross compiler GCC 14.2
  (the compiler version on Raspberry Pi OS Trixie), and that ARM64 binary runs
  under qemu-user against the simulated screen and keys: menus, Chinese fonts,
  joystick, and a KEY3 device that appears only after start-up.
- Two fresh `DIY=1` wallets got different recovery phrases. The stock emulator gave
  the same phrase both times.
- `hwrng-check` against a simulated Pi (fake sysfs, `/proc/iomem` and
  register file), also as an ARM64 build under qemu: accepts the Broadcom
  setting; refuses a changed one (2x speed, clock bypass, off), a generator
  that produces nothing and a non-Broadcom generator, leaving the register
  file unchanged every time (it is mapped read-only, so a write would
  crash the program). `--test` passes `/dev/urandom` (998 of 1000 blocks) and
  fails a stuck and a biased source on every block.
- The HAT build refuses to start when the active generator's driver is not
  `bcm2835-rng`.
- Switching between `DIY=1`, `DIY_HAT=1` and `BITCOIN_ONLY=1` without
  cleaning rebuilds everything, and both resulting programs run.
- With the hardware generator replaced by a stuck source (`/dev/zero`), the
  repetition count test stops the wallet and the HAT build shows "Wallet
  stopped / Random number generator failed". A missing `/dev/hwrng` and
  `/dev/urandom` put in its place are both refused.
- The DIY build listens on 127.0.0.1 only; the stock build on 0.0.0.0.
- The HAT build runs end to end against a simulated framebuffer and key device
  (`LD_PRELOAD` shim): menus render correctly and keys navigate wallet creation.
- `usb-relay.py` passed an `Initialize` request through to the wallet and the
  `Features` reply back, unchanged (endpoint files simulated with FIFOs), and
  still did after the wallet was restarted mid-session.
- End-to-end wallet test (`diy/test/`, see its README) on the `DIY_HAT=1`
  build, x86-64 and the ARM64 build under qemu-user. A wallet was created on
  the device with the joystick: 12 words read off the screen, word check
  passed, PIN set; after a restart the PIN unlocked it. Then, over the
  wallet's UDP port with OneKey's trezorlib (the app's protocol): Bitcoin
  addresses and account keys (legacy, nested and native SegWit, Taproot,
  mainnet and testnet, a second account), Ethereum, Tron and Solana
  addresses, a SegWit and a Taproot testnet transaction, an EIP-1559
  Ethereum transaction, an Ethereum and a Bitcoin message, each confirmed
  with the wallet's key. Every result was checked against keys derived from
  the 12 words with embit, eth-account and eth-keys: 28 of 28 pass.
- That test found a bug in the stock emulator code: after deriving a key in
  software it leaves the public key empty, because on a real Classic 1S the
  secure element fills it in. Ethereum and Tron addresses came out wrong (the
  same address for every account) and SegWit transactions carried an
  all-zero public key, which the network rejects. Fixed in `fsm.c` and
  `signing.c`, for emulator builds only. Before the fix 7 of the 28 checks
  failed.
- Where the recovery phrase's randomness comes from, on the `DIY_HAT=1`
  build (`diy/test/entropy_check.py`): with `/dev/hwrng` and `getrandom()`
  replaced by known data and every read logged, the 12 words the wallet
  showed were exactly the output of the process described above, recomputed
  independently (block 31 of the stream). The same data gave the same words
  again; changing only the hardware data, or only the kernel data, changed
  them. So nothing else (time, process state) goes into the phrase. Creating
  a wallet from the computer (`ResetDevice`) is refused by this firmware;
  wallets are only created on the device.
- The storage file, opened with the reference Python model of the storage
  format (`diy/test/storage_check.py`): the phrase is not in it as text; it
  can be read with the right PIN on the same board, and not with a wrong PIN
  or another board's salt.
- PSBT (`diy/test/psbt_check.py`, 16 checks): genuine Taproot change is
  hidden and signed correctly; an attacker's output labelled as change is
  shown (before the fix it was hidden), and so is this wallet's own address
  outside the inputs' account (account 5, index 2,000,000, chain 2, P2WPKH);
  an 80-byte OP_RETURN shows its data without the length byte; a malformed
  OP_RETURN, a negative amount, an empty Taproot leaf script and a 20-level
  key path are refused and the wallet keeps running; a Taproot script-path
  signature verifies against the untweaked key.
- Nostr (`diy/test/nostr_check.py`, 19 checks): public key, event id and
  signature, Schnorr signature, and NIP-04 encryption in both directions
  against an independent implementation, at the padding and size edges;
  over-long messages, peer keys and IVs, bad padding and a short hash are
  refused.
- Screen against signature (`diy/test/display_check.py`, 9 checks): a
  1536-byte Ethereum data field signs correctly; a first data chunk shorter
  than the data is refused; a Tron contract call carrying 5 TRX shows the
  amount and signs; TRX or a TRC-10 amount with a token transfer is refused;
  a Solana transfer and a stake withdrawal sign correctly and the withdrawal
  shows its destination; a message with a hidden zero byte is shown as hex.
- Auto-lock (`diy/test/autolock_check.py`, production build, delay set to one
  minute from the app): an address prompt left open for 100 s stays on screen
  and can still be confirmed; the wallet locks 80 s after the last activity on
  the home screen and in the menus; key presses in the menus keep it open.
- Addresses for ten more networks (`diy/test/addresses_check.py`) match ones
  derived independently from the same words (BIP-32 with embit, SLIP-10 for
  ed25519, each network's own encoding): Litecoin (legacy and SegWit),
  Dogecoin, Cosmos, XRP, Filecoin, Stellar, Aptos, Sui, Near and Algorand.
- Every one of the 353 message types the client library knows was sent once
  with minimal contents (`diy/test/message_sweep.py`): no crash, no freeze.
  (A few, such as Stellar operations outside a signing session, get no reply,
  by design; the wallet keeps answering.)
- The checks above, the entropy check and the 28-item end-to-end test all
  pass on the final build, on x86-64 and on the ARM64 build under qemu-user.
- Trezor's device test suite (`tests/device_tests`, with
  `DIY_TEST_BUILD_WITH_DEBUG_LINK=1`, run file by file against a fresh wallet,
  with a key press sent through the screen controller every 1.5 seconds for
  screens that ignore the debug interface): 353 tests pass on this release,
  including Bitcoin and
  Ethereum tests that compare addresses and signed transactions byte for byte
  with Trezor's expected values. The other runnable tests fail on differences
  in OneKey's screens and prompts, messages this firmware does not accept in
  that form, and error texts; none of the failures was a wrong address or
  signature. Some Bitcoin tests pass or fail from run to run on the same build
  (a timing problem in the test harness with OneKey's screens). An earlier
  build had 354; the two Change PIN tests that differ fail the same way on
  that build when it is run the same way (the key presses land during PIN
  entry).
- Every change in OneKey's later releases up to 3.21.0 was reviewed for fixes
  to code this build uses; those that apply are included (see the table
  above), and the whole change set was reviewed again independently.
- `app-skip-genuine-check.patch` applies to app-monorepo tag v6.6.3.
- `set-home-icon.py` reproduces the existing icon data byte for byte.

Not yet checked on real hardware:

- Building natively on a Raspberry Pi and how long it takes on a Pi Zero 2 W
  (expected 1 to 2 hours: one job, `-O3`).
- How much entropy the Pi's generator really delivers. The health tests assume
  at least 4 bits per byte; if the real source were weaker, they would stop the
  wallet (safe, but it would need looking into). Measure it with
  `rng-assess.sh` and NIST's `ea_non_iid`.
- What `RNG_CTRL` the Raspberry Pi boot firmware leaves on a Pi Zero 2 W, and
  that `/dev/mem` mapping of the RNG block works on the current Raspberry Pi OS
  kernel (its configuration has `CONFIG_STRICT_DEVMEM` off). If the value is
  not 0x00000001, or the mapping doesn't work, `hwrng-check` fails and the
  wallet won't start, which is the safe outcome.
- The `sh1106-spi` framebuffer orientation on the Waveshare HAT.
- The USB gadget on a Pi: endpoint addresses as seen by the computer
  (`lsusb -v` should show 0x81 and 0x01), Windows driver binding (the
  `DeviceInterfaceGUID` property is sent as REG_SZ, which FunctionFS supports),
  and the OneKey app finding the device over WebUSB.
- Whether `fb_sh1106` or `ssd130x` binds to the screen on current Raspberry Pi
  OS kernels. Both are accepted.
- The OneKey app itself driving the wallet. The test speaks the app's
  protocol through OneKey's Python library, not through the app.
- Entering a passphrase on the device (passphrase wallets' addresses are
  checked, see `passphrase_check.py`), and signing for coins other than
  Bitcoin, Ethereum, Tron, Solana and Nostr against independent results. Their code was reviewed
  and fixed, and ten of them were checked for correct addresses, but their
  signatures were not compared with independent results. Cardano and
  Alephium are refused by this build; Polkadot is not (its addresses and
  signatures were not checked against independent results either).

## Known limits

- If `hwrng-check` refuses the generator, the wallet service does not start:
  the screen stays blank and the reason is in `journalctl -u diywallet`.
  Nothing can be created, which is the safe outcome, but the screen doesn't
  say why.
- Coin signatures use deterministic nonces (RFC 6979 for ECDSA; BIP-340 and
  Ed25519 derive theirs from the key and message), with random numbers only
  for blinding. CoSi signing, a Trezor co-signing feature the OneKey app does
  not use, takes a random nonce for every signature.
- This branch is based on the `emulator` branch (firmware 3.12.0, June 2025).
  `master` (3.21.0) does not build as an emulator: its PIN and passphrase code
  now depends on the secure element. Fixes from 3.13 to 3.21 that apply to code
  this build uses are backported instead (see above).
- No secure element. The stored wallet is protected by the PIN only, against
  someone who takes the memory card. Use a passphrase as well, or `--ram-only`.
- The official OneKey app will show the device as not genuine. That is correct.
