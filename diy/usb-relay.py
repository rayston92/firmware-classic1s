#!/usr/bin/env python3
"""
DIY build: make the Raspberry Pi show up on your computer as a USB wallet.

The wallet program (onekey_emu.elf) talks over a UDP socket that only
programs on this Pi can reach. This script presents the Pi's USB port to the
computer as a device with the same USB identity and endpoints as a OneKey
Classic 1S, and passes each 64-byte packet between USB and that socket.
It does not look inside the packets.

Run by diywallet-usb.service after usb-gadget.sh has prepared the port;
this script then writes the descriptors and attaches the port.
Standard library only, so it can be read in one sitting.

License: GNU GPL v3 or later.
"""

import os
import select
import socket
import struct
import sys
import threading
import time

FFS_DIR = os.environ.get("DIY_FFS_DIR", "/dev/ffs-diywallet")
GADGET_DIR = os.environ.get("DIY_GADGET_DIR", "/sys/kernel/config/usb_gadget/diywallet")
WALLET_ADDR = ("127.0.0.1", int(os.environ.get("DIY_WALLET_PORT", "54935")))
PACKET = 64

# ---------------------------------------------------------------------------
# USB descriptors (Linux FunctionFS format, see Documentation/usb/functionfs.rst)
# ---------------------------------------------------------------------------
FUNCTIONFS_DESCRIPTORS_MAGIC_V2 = 3
FUNCTIONFS_STRINGS_MAGIC = 2
HAS_FS_DESC, HAS_HS_DESC, HAS_MS_OS_DESC = 1, 2, 8

# Same GUID the Classic 1S firmware gives Windows (legacy/winusb_defs.h).
WINUSB_GUID = "{0263b512-88cb-4136-9613-5c8e109d8ef5}"


def interface_desc():
    # One vendor-specific interface with two endpoints, like the real device's main interface.
    return struct.pack("<BBBBBBBBB", 9, 4, 0, 0, 2, 0xFF, 0x00, 0x00, 1)


def endpoint_desc(address, interval):
    # Interrupt endpoint, 64-byte packets.
    return struct.pack("<BBBBHB", 7, 5, address, 0x03, PACKET, interval)


def ms_os_descriptors():
    # Tells Windows to use its built-in WinUSB driver, as the real device does.
    # Header: interface, dwLength (header + body), bcdVersion, wIndex, count.
    header_size = struct.calcsize("<BIHHBB")  # 11 bytes

    compat = struct.pack("<BB8s8s6s", 0, 0, b"WINUSB", b"", b"")
    compat_header = struct.pack("<BIHHBB", 0, header_size + len(compat), 0x0100, 4, 1, 0)

    # FunctionFS takes the property name and a string value in UTF-8 and
    # converts them to UTF-16 for Windows itself (drivers/usb/gadget/function/
    # f_fs.c, composite.c fill_ext_prop). It handles REG_SZ (type 1) but not
    # REG_MULTI_SZ, so use the single-string name WinUSB also accepts.
    name = b"DeviceInterfaceGUID\0"
    data = WINUSB_GUID.encode("ascii") + b"\0"
    prop = struct.pack("<IIH", 14 + len(name) + len(data), 1, len(name)) + name
    prop += struct.pack("<I", len(data)) + data
    prop_header = struct.pack("<BIHHH", 0, header_size + len(prop), 0x0100, 5, 1)
    return [compat_header + compat, prop_header + prop]


def descriptors_blob():
    fs = [interface_desc(), endpoint_desc(0x81, 1), endpoint_desc(0x01, 1)]
    hs = [interface_desc(), endpoint_desc(0x81, 4), endpoint_desc(0x01, 4)]  # 4 = every 1 ms at high speed
    os_descs = ms_os_descriptors()
    body = struct.pack("<III", len(fs), len(hs), len(os_descs))
    body += b"".join(fs) + b"".join(hs) + b"".join(os_descs)
    flags = HAS_FS_DESC | HAS_HS_DESC | HAS_MS_OS_DESC
    return struct.pack("<III", FUNCTIONFS_DESCRIPTORS_MAGIC_V2, 12 + len(body), flags) + body


def strings_blob():
    text = b"DIY Wallet Interface\0"
    body = struct.pack("<II", 1, 1) + struct.pack("<H", 0x0409) + text
    return struct.pack("<II", FUNCTIONFS_STRINGS_MAGIC, 8 + len(body)) + body


# FunctionFS events read from ep0 (struct usb_functionfs_event is 12 bytes).
EVENT_SIZE = 12
BIND, UNBIND, ENABLE, DISABLE, SETUP, SUSPEND, RESUME = range(7)


def log(msg):
    print(msg, flush=True)


def attach_port():
    """Connect the gadget to the Pi's USB port (only possible once descriptors are written)."""
    udc_file = os.path.join(GADGET_DIR, "UDC")
    if not os.path.exists(udc_file):
        return  # test runs without configfs
    ports = sorted(os.listdir("/sys/class/udc"))
    if not ports:
        sys.exit("No USB device port found. Check 'dtoverlay=dwc2,dr_mode=peripheral' in config.txt.")
    with open(udc_file, "w") as f:
        f.write(ports[0])
    log(f"Attached to USB port {ports[0]}.")


class Relay:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.connect(WALLET_ADDR)
        self.ep_in = None   # to the computer
        self.ep_out = None  # from the computer
        self.lock = threading.Lock()

    def open_endpoints(self):
        with self.lock:
            self.close_endpoints()
            self.ep_in = os.open(os.path.join(FFS_DIR, "ep1"), os.O_WRONLY)
            self.ep_out = os.open(os.path.join(FFS_DIR, "ep2"), os.O_RDONLY)
        threading.Thread(target=self.usb_to_wallet, args=(self.ep_out,), daemon=True).start()
        log("Connected to the computer.")

    def close_endpoints(self):
        for fd in (self.ep_in, self.ep_out):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.ep_in = self.ep_out = None

    def usb_to_wallet(self, fd):
        while True:
            try:
                data = os.read(fd, PACKET)
            except OSError:
                return  # endpoint closed: the computer went away
            if data:
                try:
                    self.sock.send(data)
                except OSError:
                    # The wallet is restarting (its port is closed for a
                    # moment). Drop the packet; the app sends it again.
                    pass

    def wallet_to_usb(self):
        while True:
            try:
                data = self.sock.recv(PACKET)
            except OSError:
                # An earlier packet hit the wallet's port while it was
                # restarting. Keep listening; it will be back in a moment.
                time.sleep(0.1)
                continue
            with self.lock:
                fd = self.ep_in
            if fd is None:
                continue  # nobody connected; drop the reply
            try:
                os.write(fd, data)
            except OSError:
                pass


def main():
    ep0 = os.open(os.path.join(FFS_DIR, "ep0"), os.O_RDWR)
    os.write(ep0, descriptors_blob())
    os.write(ep0, strings_blob())
    log("USB descriptors written.")
    attach_port()

    relay = Relay()
    threading.Thread(target=relay.wallet_to_usb, daemon=True).start()

    while True:
        event = os.read(ep0, EVENT_SIZE * 4)
        for i in range(0, len(event), EVENT_SIZE):
            kind = event[i + 8]
            if kind == ENABLE:
                relay.open_endpoints()
            elif kind in (DISABLE, UNBIND):
                with relay.lock:
                    relay.close_endpoints()
                log("Computer disconnected.")
            elif kind == SETUP:
                # We define no requests of our own. Refuse ("stall") by
                # transferring in the opposite direction to the request.
                device_to_host = event[i] & 0x80
                try:
                    if device_to_host:
                        os.read(ep0, 0)
                    else:
                        os.write(ep0, b"")
                except OSError:
                    pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
