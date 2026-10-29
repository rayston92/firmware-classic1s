# The guide, rehearsed on Raspberry Pi OS

The other checks in `diy/test` run the wallet on a PC. These scripts run the
guide's steps 3 to 5 on the real Raspberry Pi OS image, with its own kernel,
systemd and udev, on an emulated Raspberry Pi (QEMU `raspi3b`, the same chip
family as the Zero 2 W). They were used on 29 October 2026 with
`2026-10-06-raspios-trixie-arm64-lite.img.xz` (kernel 6.18.50+rpt-rpi-v8,
systemd 257).

## What an emulated Pi cannot show

- The screen itself: QEMU's Pi has no working SPI or I2C controller, so the
  SH1106 cannot be attached the way the HAT attaches it, and QEMU ignores
  `config.txt`. For most stages the wallet draws on QEMU's framebuffer
  (`DIY_OLED_FB=/dev/fb0`, emulator only). Stage H instead puts the real
  screen driver (`ssd130x`) on a bit-banged I2C bus on two spare GPIO pins
  (`oled-test.dts`): nothing answers on that bus, but every message the
  driver sends can be counted, so it shows whether the wallet's pictures
  reach the screen, not what they look like.
- Pressing the joystick and keys: the `gpio-key` overlays load (stage H)
  and the wallet opens their input devices, but QEMU cannot press them.
- The generator itself: `/dev/hwrng`, the `bcm2835-rng` driver and the
  `/proc/iomem` entry are the real kernel's, but the numbers and the
  `RNG_CTRL` value come from QEMU's model of the chip, not from the Pi's
  boot firmware and silicon.
- The Pi's USB port: QEMU has no USB device controller. `dummy_hcd` (a
  software USB controller from the Linux source, built for the image's kernel)
  stands in for both the port and the computer. On a Pi, the dwc2 controller
  gives the relay's endpoints the addresses 0x81 and 0x01 (from reading
  `drivers/usb/dwc2/gadget.c` and `drivers/usb/gadget/epautoconf.c`, not
  from a test); `dummy_hcd` gives 0x81 and 0x02.
- How long the build takes on a Pi (here it ran under qemu-user).

## Files

| File | What it is |
|---|---|
| `user-data` | cloud-init settings standing in for Raspberry Pi Imager's (host name `diywallet`, user `you`, SSH on). Test-only password |
| `step4.sh` | Step 4, "Build the firmware", word for word as the user `you`, in a chroot of the image |
| `boot.sh` | Starts the image in QEMU |
| `guide-step5.sh` | Runs at boot inside the image (installed as `/usr/local/sbin/guide-step5.sh` with a oneshot service) and goes through step 5 across the reboots the guide asks for, plus checks of the services; stages A to H, logged to `/var/log/guide-step5.log` |
| `oled-test.dts` | Stage H only: the SH1106 driver on a bit-banged I2C bus on GPIO 22/23, applied at run time with `dtoverlay` |
| `usbhost.py` | The computer's side of the USB link, with the kernel's usbdevfs only: the device's identity, the Windows (WinUSB) descriptors, and an Initialize → Features exchange, over USB or straight to the wallet's local port |

Preparing the image was done by hand: unpack it into an 8 GB sparse file, add
`user-data` to the boot partition (mtools), copy the uncompressed kernel
(`kernel8.img`, gunzipped) and `bcm2710-rpi-3-b.dtb` out of it for QEMU's
`-kernel` and `-dtb`. `boot.sh` starts it without the initramfs and with
the kernel options QEMU needs (no USB FIQ, no hardware watchdog).

## What `guide-step5.sh` checks

- A: `sudo ./diy/install.sh --keep-radios` (here over an earlier install, as
  someone updating would), then `sudo reboot`.
- B: `systemctl status diywallet-check diywallet`; swap off and `/var/swap`
  gone after the restart; `sudo /opt/diywallet/hwrng-check --test`; the wallet
  running as `diywallet` with its device lockdown, its memory locked, reading
  `/dev/hwrng` and listening on 127.0.0.1:54935; killing it makes the checks
  run again before it restarts; with the generator switched off
  (`RNG_CTRL=0`), the wallet does not start and does not keep retrying; the
  guide's command for an upside-down screen.
- C: the final install (Wi-Fi and Bluetooth off), restart, wallet running.
- D: the `--ram-only` install: the wallet keeps its storage in `/run`.
- E: USB through `dummy_hcd`: identity `1209:4f4b`, maker "DIY", WinUSB
  descriptors, Initialize → Features, then again after unplugging and
  plugging back in, after `sudo systemctl restart diywallet`, and after the
  wallet is killed.
- F, G: a fresh install (user, group, rule and services removed first): the
  generator ends up readable by the `diywallet` group only, and the wallet
  starts after a restart.
- H: the `gpio-key` lines from `config.txt` and `oled-test.dts`, applied at
  run time with `dtoverlay`. With the HDMI framebuffer as `/dev/fb0` (here the
  firmware's, on a Pi the KMS driver's) and the OLED as `/dev/fb1`, the
  screen driver is reloaded before each case so the screen starts as after
  boot, and the I2C messages are counted: before the fix, then with only the
  new check service, only the new wallet, and both after `install.sh`.

## What it found on 29 October 2026

The first three would have stopped the wallet on every Pi; the fourth left
the screen dark on many. All four are fixed.

1. `hwrng-check` could not find the generator in `/proc/iomem`, because
   current kernels label the entry `3f104000.rng rng@7e104000`. (This one was
   first reported from a real Pi.)
2. `diywallet.service` never got past its first step. `DevicePolicy=closed`
   also covers the steps run as root with `ExecStartPre=+`, so `swapoff` could
   not open `/dev/zram0`, nor `hwrng-check` `/dev/mem`. Those steps now run in
   `diywallet-check.service`.
3. The wallet could not open `/dev/hwrng`: the udev rule matched
   `KERNEL=="hwrng"`, but the kernel calls the device `hw_random`. The rule
   now matches `hw_random`, and `install.sh` checks that the `diywallet` user
   can read the generator.
4. The screen stayed dark (first reported from a real Pi, then reproduced in
   stage H). With Raspberry Pi OS's HDMI driver, the OLED becomes `/dev/fb1`
   and the text console stays on `/dev/fb0`. `ssd130x` leaves a framebuffer
   the console doesn't use switched off until it is unblanked, and the wallet
   only wrote pixels: in stage H the driver sent nothing at all. Now
   `diywallet-check.service` unblanks it before every start (enough without
   rebuilding), and the wallet does too when it opens it.

It also found that Raspberry Pi OS 13 keeps swap on through rpi-swap, by
default compressed memory that writes idle pages to `/var/swap` on the card;
`install.sh` now switches it off for good.
