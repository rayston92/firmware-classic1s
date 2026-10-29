#!/bin/bash
# boot.sh: start the Raspberry Pi OS image on an emulated Raspberry Pi 3 (same chip family as the Zero 2 W)
P=${P:-$PWD}   # folder with pi.img, Image and the .dtb files
cd $P
exec qemu-system-aarch64 -M raspi3b -m 1G -smp 4 \
  -kernel Image -dtb ${DTB:-bcm2710-rpi-3-b.dtb} \
  -drive file=pi.img,if=sd,format=raw \
  -append "earlycon=pl011,0x3f201000 console=ttyAMA0,115200 root=/dev/mmcblk0p2 rootfstype=ext4 fsck.repair=yes rootwait dwc_otg.lpm_enable=0 dwc_otg.fiq_enable=0 dwc_otg.fiq_fsm_enable=0 dwc_otg.nak_holdoff=0 systemd.watchdog_sec=off ${EXTRA_APPEND:-}" \
  -netdev user,id=net0,hostfwd=tcp:127.0.0.1:5022-:22 -device usb-net,netdev=net0 \
  -usb \
  -display none -serial file:$P/serial.log -serial file:$P/serial1.log -monitor unix:$P/mon.sock,server,nowait
