#!/bin/sh
# DIY build: prepare (or remove) the USB device identity of the Pi's USB port.
#   usb-gadget.sh start   create the gadget and the FunctionFS mount
#   usb-gadget.sh stop    detach and remove everything again
# usb-relay.py then writes the descriptors and attaches the port.
#
# The identity matches a OneKey Classic 1S (vendor 0x1209, product 0x4F4B),
# so the OneKey app recognises it. The manufacturer string says "DIY".
#
# License: GNU GPL v3 or later.
set -eu

G=/sys/kernel/config/usb_gadget/diywallet
FFS=/dev/ffs-diywallet

stop() {
  [ -d "$G" ] || return 0
  # configfs files always report a size, so read the value instead.
  if [ -n "$(cat "$G/UDC" 2>/dev/null)" ]; then echo "" > "$G/UDC" || true; fi
  rm -f "$G/os_desc/c.1" "$G/configs/c.1/ffs.diywallet" 2>/dev/null || true
  mountpoint -q "$FFS" && umount "$FFS" || true
  rmdir "$G/configs/c.1/strings/0x409" "$G/configs/c.1" 2>/dev/null || true
  rmdir "$G/functions/ffs.diywallet" "$G/strings/0x409" "$G" 2>/dev/null || true
}

start() {
  stop
  modprobe libcomposite
  mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config

  mkdir -p "$G"
  echo 0x1209 > "$G/idVendor"
  echo 0x4F4B > "$G/idProduct"
  echo 0x0101 > "$G/bcdDevice"
  echo 0x0200 > "$G/bcdUSB"

  mkdir -p "$G/strings/0x409"
  echo "DIY" > "$G/strings/0x409/manufacturer"
  echo "DIY Wallet" > "$G/strings/0x409/product"
  # No serial number: the Pi's own serial number is used to protect storage
  # and should not be handed to every computer the wallet is plugged into.

  mkdir -p "$G/configs/c.1/strings/0x409"
  echo "Wallet" > "$G/configs/c.1/strings/0x409/configuration"
  echo 250 > "$G/configs/c.1/MaxPower"

  # Windows: ask for the built-in WinUSB driver, like the real device.
  echo 1 > "$G/os_desc/use"
  echo 0x21 > "$G/os_desc/b_vendor_code"
  echo MSFT100 > "$G/os_desc/qw_sign"

  mkdir -p "$G/functions/ffs.diywallet"
  ln -s "$G/functions/ffs.diywallet" "$G/configs/c.1/"
  ln -s "$G/configs/c.1" "$G/os_desc/c.1"

  mkdir -p "$FFS"
  mount -t functionfs diywallet "$FFS"
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  *) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
