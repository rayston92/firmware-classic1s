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
| (not applicable) | Before every start, as root: `hwrng-check` confirms `/dev/hwrng` is the Broadcom block (driver and device tree) and reads its control register through `/dev/mem`. Only `RNG_CTRL = 0x00000001` is accepted (on, all ring oscillators, internal jitter clock, normal speed), the setting Broadcom's own driver code writes; anything else is reprogrammed with Broadcom's sequence and read back. | `diy/hwrng-check.c`, `diy/diywallet.service.in` |
| Listens on every network interface | Listens on 127.0.0.1 only | `legacy/emulator/udp.c` |
| Storage encryption salt is all zeros | Salt derived from the Pi's serial number | `legacy/emulator/diy/hw_salt.c`, `legacy/firmware/trezor.c` |
| Screen size 256x128 on this branch | The real Classic size, 128x64 | `legacy/oled.h` |
| Memory can be swapped out and core-dumped | Memory locked in RAM (`mlockall`), no core dumps, not debuggable by other processes | `legacy/emulator/diy/harden.c`, `legacy/emulator/setup.c` |
| `-Werror` | Warnings are shown but don't stop the build (upstream CI only builds on x86) | `legacy/Makefile.include` |
| Changing build options reuses objects built with the old ones | The options are remembered in `legacy/.diy-flags`; when they change, everything is rebuilt and the libraries are recreated | `legacy/Makefile.include`, `legacy/Makefile`, `legacy/emulator/Makefile`, `legacy/firmware/protob/Makefile` |

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
| `hwrng-check.c` | Root helper: checks the generator is the Pi's Broadcom block with Broadcom's register settings (repairs them if not). `hwrng-check --test` also runs the FIPS 140-2 statistical tests on 1000 blocks of raw output |
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
  setting, reprograms a changed one (2x speed, clock bypass) to 0x00000001,
  refuses a non-Broadcom generator. `--test` passes `/dev/urandom` (998 of
  1000 blocks) and fails a stuck and a biased source on every block.
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
  kernel (its configuration has `CONFIG_STRICT_DEVMEM` off). If it doesn't,
  `hwrng-check` fails and the wallet won't start, which is the safe outcome.
- The `sh1106-spi` framebuffer orientation on the Waveshare HAT.
- The USB gadget on a Pi: endpoint addresses as seen by the computer
  (`lsusb -v` should show 0x81 and 0x01), Windows driver binding (the
  `DeviceInterfaceGUID` property is sent as REG_SZ, which FunctionFS supports),
  and the OneKey app finding the device over WebUSB.
- Whether `fb_sh1106` or `ssd130x` binds to the screen on current Raspberry Pi
  OS kernels. Both are accepted.
- The OneKey app itself driving the wallet. The test speaks the app's
  protocol through OneKey's Python library, not through the app.
- Passphrase wallets, and coins other than Bitcoin, Ethereum, Tron and Solana.

## Known limits

- This branch is based on the `emulator` branch (firmware 3.12.0, June 2025).
  `master` (3.21.0) does not build as an emulator today.
- No secure element. The stored wallet is protected by the PIN only, against
  someone who takes the memory card. Use a passphrase as well, or `--ram-only`.
- The official OneKey app will show the device as not genuine. That is correct.
