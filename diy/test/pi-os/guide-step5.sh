#!/bin/bash
# Test harness, only in the emulated Pi: run the guide's step 5 commands as the
# user "you", the way a reader types them over SSH, across the reboots the
# guide asks for, plus a few checks of the services. Logged to /dev/kmsg and
# /var/log/guide-step5.log.
LOG=/var/log/guide-step5.log
STATE=/var/lib/guide-step5
mkdir -p $STATE
exec > >(while IFS= read -r l; do echo "STEP5: $l" > /dev/kmsg; echo "$l" >> $LOG; done) 2>&1
sleep 45
stage=$(cat $STATE/stage 2>/dev/null || echo A)
as_you() { sudo -u you -H bash -lc "$1"; }
checkruns() { journalctl -u diywallet-check -b --no-pager -o cat | grep -c "Finished diywallet-check"; }
ctrl() { python3 - "$@" <<'PY'
import mmap, os, struct, sys
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x3f104000)
if len(sys.argv) > 1: struct.pack_into("<I", m, 0, int(sys.argv[1], 0))
print("RNG_CTRL now 0x%08x" % struct.unpack_from("<I", m, 0)[0])
PY
}
echo "=== stage $stage, $(date -u), kernel $(uname -r)"

case $stage in
A)
  echo "--- Install (update over the earlier install), Wi-Fi still on: cd ~/wallet; sudo ./diy/install.sh --keep-radios"
  as_you 'cd ~/wallet && git log -1 --format="source: %h %s" && sudo ./diy/install.sh --keep-radios'
  echo "install exit: $?"
  ls -l /etc/systemd/system/diywallet*.service; cat /etc/rpi/swap.conf.d/90-diywallet-no-swap.conf
  echo B > $STATE/stage
  echo "--- sudo reboot"
  sync
  as_you 'sudo reboot'
  ;;
B)
  echo "--- If the screen stays dark: systemctl status diywallet-check diywallet"
  as_you 'systemctl status diywallet-check diywallet --no-pager -l' | head -40
  echo "--- check runs so far this boot: $(checkruns)"
  journalctl -u diywallet -b --no-pager -o cat | tail -8
  echo "--- swap after boot (expect nothing): swapon --show"; swapon --show; ls -l /var/swap 2>&1
  echo "--- grep rng /proc/iomem"; grep -i rng /proc/iomem
  echo "--- Check the random number generator: sudo /opt/diywallet/hwrng-check --test"
  as_you 'sudo /opt/diywallet/hwrng-check --test'
  echo "hwrng-check exit: $?"
  echo "--- (emulator only) point the wallet at the emulated display, since there is no SH1106 screen here"
  echo "DIY_OLED_FB=/dev/fb0" > /etc/diywallet.conf
  systemctl restart diywallet; sleep 60
  systemctl status diywallet --no-pager -l | head -14
  journalctl -u diywallet -b --no-pager -o cat | tail -12
  echo "--- the wallet's process and what it may open"
  ps -o user,pid,cmd -C wallet
  systemctl show diywallet -p MainPID -p User -p DeviceAllow -p DevicePolicy -p MemoryDenyWriteExecute -p NRestarts
  ls -l /proc/$(systemctl show diywallet -p MainPID --value)/fd 2>/dev/null | awk '{print $NF}' | sort | uniq -c
  grep -E "VmLck|VmRSS" /proc/$(systemctl show diywallet -p MainPID --value)/status
  echo "--- the wallet answers on its local port (what usb-relay.py talks to)"
  ss -lunp | grep -E "54935|wallet" || ss -lunp
  echo "--- kill the wallet: the checks must run again before it restarts"
  before=$(checkruns); pid1=$(systemctl show diywallet -p MainPID --value)
  systemctl kill -s KILL diywallet; sleep 15
  pid2=$(systemctl show diywallet -p MainPID --value)
  echo "wallet pid $pid1 -> $pid2, active: $(systemctl is-active diywallet), check runs $before -> $(checkruns)"
  echo "--- switch the emulated generator off: the wallet must not start, and must not loop"
  ctrl 0
  before=$(checkruns)
  systemctl restart diywallet; sleep 20
  echo "wallet: $(systemctl is-active diywallet), check: $(systemctl is-active diywallet-check), check runs $before -> $(checkruns)"
  journalctl -u diywallet-check -u diywallet -b --no-pager -o cat | tail -8
  sleep 20; echo "20 s later, wallet: $(systemctl is-active diywallet), NRestarts=$(systemctl show diywallet -p NRestarts --value)"
  as_you 'systemctl status diywallet-check diywallet --no-pager -l' | head -30
  echo "--- generator back on, start the wallet"
  ctrl 1
  systemctl start diywallet; sleep 30
  echo "wallet: $(systemctl is-active diywallet)"
  journalctl -u diywallet -b --no-pager -o cat | grep -i -E "random|generator" | tail -3
  echo "--- If the picture is upside down: echo DIY_OLED_ROTATE=180 | sudo tee /etc/diywallet.conf; sudo systemctl restart diywallet"
  echo "DIY_OLED_FB=/dev/fb0" > /etc/diywallet.conf   # emulator only
  as_you 'echo DIY_OLED_ROTATE=180 | sudo tee -a /etc/diywallet.conf; sudo systemctl restart diywallet'; sleep 20
  echo "wallet: $(systemctl is-active diywallet)"; journalctl -u diywallet -b --no-pager -o cat | tail -3
  echo "--- systemctl status diywallet-usb, and its log"
  systemctl status diywallet-usb --no-pager -l | head -14
  journalctl -u diywallet-usb -b --no-pager -o cat | grep -v -E "^(Starting|Started|Stopping|Stopped)" | sort | uniq -c | sort -rn | head -12
  ls /sys/class/udc/ 2>&1
  echo C > $STATE/stage
  echo "--- Final install, radios off: cd ~/wallet; sudo ./diy/install.sh; sudo reboot"
  as_you 'cd ~/wallet && sudo ./diy/install.sh'
  echo "install exit: $?"
  grep -A20 ">>> diywallet" /boot/firmware/config.txt | grep -E "wifi|bt|Wi-Fi"
  sync
  as_you 'sudo reboot'
  ;;
C)
  echo "--- after the final install"
  systemctl is-enabled diywallet diywallet-usb
  echo "--- swap (expect nothing)"; swapon --show; echo "swap lines: $(swapon --show | wc -l)"; ls -l /var/swap 2>&1
  echo "--- check runs this boot: $(checkruns); last check output:"
  journalctl -u diywallet-check -b --no-pager -o cat | grep -v -E "^(Starting|Finished|diywallet-check.service: Deactivated)" | tail -4
  echo "--- wallet after the final install: $(systemctl is-active diywallet), NRestarts=$(systemctl show diywallet -p NRestarts --value)"
  journalctl -u diywallet -b --no-pager -o cat | tail -4
  ls -l /dev/hwrng; ls -l /var/lib/diywallet/
  echo "--- RAM-only variant: cd ~/wallet; sudo ./diy/install.sh --ram-only; sudo reboot"
  as_you 'cd ~/wallet && sudo ./diy/install.sh --ram-only'
  echo "install exit: $?"
  echo D > $STATE/stage
  sync
  as_you 'sudo reboot'
  ;;
D)
  echo "--- after the RAM-only install"
  echo "wallet: $(systemctl is-active diywallet), NRestarts=$(systemctl show diywallet -p NRestarts --value)"
  systemctl show diywallet -p WorkingDirectory -p RuntimeDirectory -p StateDirectory
  ls -l /run/diywallet/ 2>&1
  journalctl -u diywallet -b --no-pager -o cat | tail -4
  swapon --show; echo "swap lines: $(swapon --show | wc -l)"
  rm -f /etc/diywallet.conf
  echo E > $STATE/stage
  echo "=== GUIDE-STEP5 DONE"
  systemctl poweroff
  ;;
E)
  echo "--- USB, with a software USB controller (dummy_hcd, built for this kernel) standing in for the Pi's USB port and the computer"
  echo "DIY_OLED_FB=/dev/fb0" > /etc/diywallet.conf   # emulator only
  systemctl restart diywallet; sleep 20
  insmod /root/dummy/dummy_hcd.ko; sleep 3; echo "USB device ports: $(ls /sys/class/udc)"
  systemctl restart diywallet-usb; sleep 15
  echo "relay: $(systemctl is-active diywallet-usb)"; journalctl -u diywallet-usb -b --no-pager -o cat | tail -4
  echo "--- 1. straight to the wallet's local port"; python3 /root/usbhost.py udp
  echo "--- 2. over USB"; python3 /root/usbhost.py usb
  dev=$(dirname $(grep -l -i 4f4b /sys/bus/usb/devices/*/idProduct | head -1))
  echo "--- 3. unplug and plug back in ($dev)"
  echo 0 > $dev/authorized; sleep 3; echo 1 > $dev/authorized; sleep 5
  python3 /root/usbhost.py usb
  echo "--- 4. restart the wallet by hand: sudo systemctl restart diywallet"
  as_you 'sudo systemctl restart diywallet'; sleep 25
  echo "wallet: $(systemctl is-active diywallet), relay: $(systemctl is-active diywallet-usb)"
  python3 /root/usbhost.py usb
  echo "--- 5. the wallet crashes (killed), systemd restarts it"
  systemctl kill -s KILL diywallet; sleep 25
  echo "wallet: $(systemctl is-active diywallet), relay: $(systemctl is-active diywallet-usb)"
  python3 /root/usbhost.py usb
  echo "--- relay log"
  journalctl -u diywallet-usb -b --no-pager -o cat | grep -v -E "^(Starting|Started|Stopping|Stopped)" | tail -14
  rm -f /etc/diywallet.conf
  echo F > $STATE/stage
  echo "=== GUIDE-STEP5 USB DONE"
  systemctl poweroff
  ;;
F)
  echo "--- Fresh install: remove everything the install made, then install again"
  systemctl disable --now diywallet diywallet-usb 2>&1 | tail -2
  rm -f /etc/systemd/system/diywallet*.service /etc/udev/rules.d/90-diywallet-hwrng.rules /etc/rpi/swap.conf.d/90-diywallet-no-swap.conf
  rm -rf /opt/diywallet /var/lib/diywallet; userdel diywallet; systemctl daemon-reload
  udevadm control --reload-rules; udevadm trigger --name-match=/dev/hwrng; udevadm settle
  echo "before: $(stat -c '%U:%G %a' /dev/hwrng), user: $(id diywallet 2>&1)"
  as_you 'cd ~/wallet && sudo ./diy/install.sh --keep-radios'
  echo "install exit: $?"
  echo "after: $(stat -c '%U:%G %a' /dev/hwrng), user: $(id diywallet)"
  echo "DIY_OLED_FB=/dev/fb0" > /etc/diywallet.conf   # emulator only
  echo G > $STATE/stage
  sync
  as_you 'sudo reboot'
  ;;
G)
  echo "--- after the fresh install and a restart"
  echo "wallet: $(systemctl is-active diywallet), NRestarts=$(systemctl show diywallet -p NRestarts --value), hwrng: $(stat -c '%U:%G %a' /dev/hwrng)"
  journalctl -u diywallet-check -u diywallet -b --no-pager -o cat | grep -E "Randomness|Generator settings|cannot|failed" | head -4
  swapon --show; echo "swap lines: $(swapon --show | wc -l)"
  python3 /root/usbhost.py udp
  rm -f /etc/diywallet.conf
  echo H > $STATE/stage
  echo "=== GUIDE-STEP5 FRESH DONE"
  systemctl poweroff
  ;;
H)
  echo "--- Screen and keys through the real drivers: overlays applied at run time (QEMU ignores config.txt)"
  systemctl stop diywallet-usb diywallet
  for k in "5 105 joystick-left" "26 106 joystick-right" "6 103 joystick-up" "19 108 joystick-down" "13 28 joystick-press" "21 28 key1" "16 1 key3"; do
    set -- $k; dtoverlay gpio-key gpio=$1 keycode=$2 label=$3
  done
  sleep 3; echo "key devices: $(grep -c -i 'N: Name="button@' /proc/bus/input/devices) ($(grep -o 'N: Name="button@[0-9a-f]*"' /proc/bus/input/devices | tr '\n' ' '))"
  dtoverlay -d /root oled-test; sleep 10
  for f in /sys/class/graphics/fb*; do echo "${f##*/}: $(cat $f/name)"; done
  dmesg | grep -i -E "ssd130x|fb[0-9]+:" | tail -4
  OLED=$(grep -l -E "sh1106|ssd130x" /sys/class/graphics/fb*/name | head -1 | xargs dirname)
  BUS=$(ls -d /sys/bus/i2c/devices/*-003c | head -1 | sed 's|.*/||; s|-.*||')
  DRI=$(grep -l ssd130x /sys/kernel/debug/dri/*/name | head -1 | xargs dirname)
  T=/sys/kernel/tracing; echo 1 > $T/events/i2c/i2c_write/enable
  echo "OLED framebuffer: $OLED, I2C bus $BUS, DRM debug dir $DRI"
  state() { echo "crtc $(grep -o -m1 'active=[01]' $DRI/state), plane $(grep -m1 -o 'fb=[0-9]*' $DRI/state)"; }
  fresh() {  # reload the screen driver, so the screen is as just after boot: never switched on
    echo $BUS-003c > /sys/bus/i2c/drivers/ssd130x-i2c/unbind; sleep 1; echo $BUS-003c > /sys/bus/i2c/drivers/ssd130x-i2c/bind; sleep 3
    OLED=$(grep -l -E "sh1106|ssd130x" /sys/class/graphics/fb*/name | head -1 | xargs dirname)
    DRI=$(grep -l ssd130x /sys/kernel/debug/dri/*/name | head -1 | xargs dirname)
  }
  run() {  # $1 = label; the wallet and check service must already be in place
    systemctl stop diywallet; fresh; echo > $T/trace
    echo "== $1"; echo "screen ${OLED##*/} ($(cat $OLED/name)) before the start: $(state)"
    systemctl daemon-reload; systemctl start diywallet; sleep 25
    pid=$(systemctl show diywallet -p MainPID --value)
    echo "wallet: $(systemctl is-active diywallet) ($(cmp -s /opt/diywallet/wallet /root/wallet-new && echo new || echo old) program, $(grep -q blank /etc/systemd/system/diywallet-check.service && echo new || echo old) check service), opened: $(ls -l /proc/$pid/fd 2>/dev/null | grep -o '/dev/[a-z]*[0-9]*' | sort -u | tr '\n' ' ')"
    echo "after 25 s: $(state)"
    n=$((n+1)); cp $T/trace /var/log/oled-trace-$n.txt
    awk -v b="i2c-$BUS" '$0 ~ "i2c_write: "b" " { match($0,/l=[0-9]+/); l=substr($0,RSTART+2,RLENGTH-2)
        if ($0 ~ /\[80-af\]/) on++; else if ($0 ~ / f=4000 /) { d++; db+=l } else if ($0 ~ /l=1 \[40\]/) h++; else c++ }
      END {printf "sent to the screen: display-on %d time(s), %d commands, %d picture-data messages (%d bytes)\n", on, c, d, db}' $T/trace
    journalctl -u diywallet-check -b --no-pager -o cat --since "-30s" | grep -i "switched on" | tail -1
  }
  oldcheck() { cp /root/check-old.service /etc/systemd/system/diywallet-check.service; }
  newcheck() { cp /home/you/wallet/diy/diywallet-check.service /etc/systemd/system/diywallet-check.service; }
  systemctl stop diywallet
  oldcheck; install -m 755 /root/wallet-old /opt/diywallet/wallet
  run "1. before the fix (check service and wallet from 1f469eab8)"
  systemctl stop diywallet; newcheck
  run "2. new check service only (no rebuild)"
  systemctl stop diywallet; oldcheck; install -m 755 /root/wallet-new /opt/diywallet/wallet
  run "3. new wallet only"
  systemctl stop diywallet
  as_you 'cd ~/wallet && sudo ./diy/install.sh --keep-radios' | tail -2
  run "4. after the update and install, as a user would"
  echo 0 > $T/events/i2c/i2c_write/enable
  echo J > $STATE/stage
  echo "=== GUIDE-STEP5 SCREEN DONE"
  systemctl poweroff
  ;;
*)
  echo "=== GUIDE-STEP5 DONE (nothing left)"
  ;;
esac
