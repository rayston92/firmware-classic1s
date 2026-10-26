#!/bin/bash
# Install the DIY wallet on this Raspberry Pi after building it.
#   sudo ./diy/install.sh                 keep the wallet on the memory card (PIN-protected)
#   sudo ./diy/install.sh --ram-only      forget the wallet every time the Pi loses power
#   sudo ./diy/install.sh --keep-radios   don't turn off Wi-Fi and Bluetooth (not recommended)
#
# License: GNU GPL v3 or later.
set -euo pipefail
cd "$(dirname "$0")/.."

RAM_ONLY=0
KEEP_RADIOS=0
for arg in "$@"; do
  case "$arg" in
    --ram-only) RAM_ONLY=1 ;;
    --keep-radios) KEEP_RADIOS=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }

ELF=legacy/firmware/onekey_emu.elf
[ -f "$ELF" ] || { echo "No wallet program found. Build it first (see diy/README.md)." >&2; exit 1; }
if ldd "$ELF" | grep -q libSDL2; then
  echo "This is the desktop test build. Rebuild with DIY_HAT=1 (see diy/README.md)." >&2
  exit 1
fi
# A build with the debug interface could hand out the recovery phrase.
if ! grep -aq "DIY-BUILD-KIND:WALLET" "$ELF" || grep -aq "DIY-BUILD-KIND:TEST" "$ELF" ||
  grep -aq "fsm_msgDebugLinkGetState" "$ELF"; then
  echo "This program is not a wallet build (it may have the debug interface). Rebuild with DEBUG_LINK=0." >&2
  exit 1
fi

RNG=$(cat /sys/class/misc/hw_random/rng_current 2>/dev/null || true)
if [ -z "$RNG" ] || [ "$RNG" = "none" ] || [ ! -c /dev/hwrng ]; then
  echo "No hardware random number generator found (/dev/hwrng). The wallet will not run without one." >&2
  exit 1
fi
echo "Hardware random number generator: $RNG"

install -d -m 755 /opt/diywallet
install -m 755 "$ELF" /opt/diywallet/wallet
install -m 755 diy/usb-gadget.sh diy/usb-relay.py /opt/diywallet/

# The random number check: the Pi's own Broadcom generator, with Broadcom's
# register settings. Stop here if it fails; a wallet must not run without it.
cc -O2 -Wall -o /opt/diywallet/hwrng-check diy/hwrng-check.c
if ! /opt/diywallet/hwrng-check; then
  echo "Install stopped: the hardware random number generator check failed." >&2
  exit 1
fi

id diywallet >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin diywallet

# Only the wallet may read the hardware random number generator directly.
echo 'KERNEL=="hwrng", GROUP="diywallet", MODE="0440"' > /etc/udev/rules.d/90-diywallet-hwrng.rules
udevadm control --reload-rules || true
udevadm trigger --name-match=hwrng || true

if [ "$RAM_ONLY" -eq 1 ]; then
  STORAGE="RuntimeDirectory=diywallet\nRuntimeDirectoryMode=0700\nWorkingDirectory=/run/diywallet"
else
  STORAGE="StateDirectory=diywallet\nStateDirectoryMode=0700\nWorkingDirectory=/var/lib/diywallet"
fi
sed "s|@STORAGE@|$STORAGE|" diy/diywallet.service.in | sed 's/\\n/\n/g' > /etc/systemd/system/diywallet.service
install -m 644 diy/diywallet-usb.service /etc/systemd/system/diywallet-usb.service

CFG=/boot/firmware/config.txt
[ -f "$CFG" ] || CFG=/boot/config.txt
if [ "$KEEP_RADIOS" -eq 1 ]; then
  RADIOS="# Wi-Fi and Bluetooth left on (--keep-radios)."
else
  RADIOS="dtoverlay=disable-wifi\ndtoverlay=disable-bt"
fi
# Replace any earlier block, then append the current one.
sed -i '/^# >>> diywallet/,/^# <<< diywallet/d' "$CFG"
sed "s|@RADIOS@|$RADIOS|" diy/config.txt.snippet | sed 's/\\n/\n/g' >> "$CFG"

# No swap: it would let Linux write the wallet's memory to the memory card.
# The wallet service also runs "swapoff -a" each time before it starts.
swapoff -a || true
if command -v dphys-swapfile >/dev/null 2>&1; then
  dphys-swapfile swapoff || true
  systemctl disable --now dphys-swapfile.service 2>/dev/null || true
fi

systemctl daemon-reload
systemctl enable diywallet.service diywallet-usb.service

echo
echo "Installed. Restart the Pi to start the wallet:"
echo "  sudo reboot"
if [ "$KEEP_RADIOS" -eq 0 ]; then
  echo "After the restart Wi-Fi and Bluetooth are off, so you can no longer log in over the network."
fi
