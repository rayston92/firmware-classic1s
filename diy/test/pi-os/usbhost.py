#!/usr/bin/env python3
"""Emulator test only: play the computer's side of the wallet's USB link.

  usbhost.py udp        Initialize -> Features straight to the wallet's UDP port
  usbhost.py usb        the same over USB (needs the gadget attached to a UDC
                        whose host side is this machine, e.g. dummy_hcd)

USB part uses usbdevfs ioctls only (no pyusb), like libusb does underneath.
"""
import fcntl, glob, os, socket, struct, sys, time

VID, PID = 0x1209, 0x4F4B
# arm64 sizes: struct usbdevfs_ctrltransfer and usbdevfs_bulktransfer are 24 bytes
USBDEVFS_CONTROL = 0xC0185500
USBDEVFS_BULK = 0xC0185502
USBDEVFS_CLAIMINTERFACE = 0x8004550F
USBDEVFS_RELEASEINTERFACE = 0x80045510


def frame(msg_type, payload=b""):
    data = b"##" + struct.pack(">HI", msg_type, len(payload)) + payload
    pkts = []
    while data:
        pkts.append((b"?" + data[:63]).ljust(64, b"\0"))
        data = data[63:]
    return pkts


def unframe(read_packet):
    first = read_packet()
    assert first[:3] == b"?##", first[:8]
    msg_type, length = struct.unpack(">HI", first[3:9])
    data = first[9:]
    while len(data) < length:
        p = read_packet()
        assert p[:1] == b"?", p[:4]
        data += p[1:]
    return msg_type, data[:length]


def proto_strings(buf):
    """Field number -> value for the top-level fields (strings and varints)."""
    out, i = {}, 0
    while i < len(buf):
        key, i = varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            v, i = varint(buf, i)
        elif wire == 2:
            n, i = varint(buf, i)
            v = buf[i:i + n]; i += n
            try:
                v = v.decode()
            except UnicodeDecodeError:
                v = v.hex()
        elif wire == 5:
            v = buf[i:i + 4].hex(); i += 4
        elif wire == 1:
            v = buf[i:i + 8].hex(); i += 8
        else:
            break
        out.setdefault(field, v)
    return out


def varint(b, i):
    v = s = 0
    while True:
        c = b[i]; i += 1
        v |= (c & 0x7F) << s; s += 7
        if not c & 0x80:
            return v, i


def show_features(msg_type, data):
    f = proto_strings(data)
    # Features (type 17): 1 vendor, 2-4 version, 7 device_id, 10 label, 12 initialized
    print(f"reply type {msg_type} ({'Features' if msg_type == 17 else '?'}), {len(data)} bytes:"
          f" vendor={f.get(1)!r} version={f.get(2)}.{f.get(3)}.{f.get(4)}"
          f" initialized={f.get(12)} label={f.get(10)!r}")
    return msg_type == 17 and f.get(1) is not None


def test_udp(port=54935):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    s.connect(("127.0.0.1", port))
    for p in frame(0):
        s.send(p)
    return show_features(*unframe(lambda: s.recv(64)))


def find_device():
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            v = int(open(d + "/idVendor").read(), 16)
            p = int(open(d + "/idProduct").read(), 16)
        except OSError:
            continue
        if (v, p) == (VID, PID):
            r = lambda n: open(f"{d}/{n}").read().strip() if os.path.exists(f"{d}/{n}") else None
            return d, int(r("busnum")), int(r("devnum")), r("manufacturer"), r("product"), r("speed"), r("serial")
    return None


def _xfer(fd, request, head, buf, timeout):
    """usbdevfs transfer whose struct ends with a data pointer; returns bytes moved."""
    import ctypes
    b = (ctypes.c_char * len(buf)).from_buffer(buf)
    arg = bytearray(head + struct.pack("<I4xQ", timeout, ctypes.addressof(b)))
    # With a mutable buffer, fcntl.ioctl returns the ioctl's own result: the byte count.
    return fcntl.ioctl(fd, request, arg)


def control(fd, req_type, req, value, index, length, timeout=2000):
    buf = bytearray(length)
    n = _xfer(fd, USBDEVFS_CONTROL, struct.pack("<BBHHH", req_type, req, value, index, length), buf, timeout)
    return bytes(buf[:n])


def bulk(fd, ep, data_or_len, timeout=5000):
    buf = bytearray(data_or_len) if not isinstance(data_or_len, int) else bytearray(data_or_len)
    n = _xfer(fd, USBDEVFS_BULK, struct.pack("<II", ep, len(buf)), buf, timeout)
    return bytes(buf[:n])


def test_usb(wait=30):
    dev = None
    for _ in range(wait):
        dev = find_device()
        if dev:
            break
        time.sleep(1)
    if not dev:
        print("no USB device 1209:4f4b found")
        return False
    path, bus, num, manu, prod, speed, serial = dev
    print(f"device {VID:04x}:{PID:04x} at {path}: manufacturer={manu!r} product={prod!r} speed={speed} serial={serial!r}")
    fd = os.open(f"/dev/bus/usb/{bus:03d}/{num:03d}", os.O_RDWR)
    try:
        # Windows asks for string 0xEE first, then the vendor request it names.
        s = control(fd, 0x80, 6, 0x03EE, 0, 18)
        sig = s[2:16].decode("utf-16-le", "replace")
        print(f"MS OS string descriptor: {sig!r}, vendor code 0x{s[16]:02x}")
        c = control(fd, 0xC0, s[16], 0, 4, 40)
        print(f"compatible ID: {c[18:26].rstrip(bytes(1))!r}")
        # The UDC assigns endpoint numbers (dwc2 on a Pi: 0x81/0x01). Read them.
        eps = {}
        for e in glob.glob(f"{path}:1.0/ep_*"):
            eps[open(e + "/direction").read().strip()] = (int(open(e + "/bEndpointAddress").read(), 16),
                                                          open(e + "/type").read().strip())
        print(f"interface 0 endpoints: in=0x{eps['in'][0]:02x} ({eps['in'][1]}), out=0x{eps['out'][0]:02x} ({eps['out'][1]})")
        ep_in, ep_out = eps["in"][0], eps["out"][0]
        fcntl.ioctl(fd, USBDEVFS_CLAIMINTERFACE, struct.pack("<I", 0))
        for p in frame(0):
            bulk(fd, ep_out, p)
        ok = show_features(*unframe(lambda: bulk(fd, ep_in, 64)))
        fcntl.ioctl(fd, USBDEVFS_RELEASEINTERFACE, struct.pack("<I", 0))
        return ok and sig == "MSFT100" and c[18:24] == b"WINUSB"
    finally:
        os.close(fd)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "usb"
    try:
        ok = test_udp() if mode == "udp" else test_usb()
    except Exception as e:  # report, don't hide
        print(f"{mode} test error: {type(e).__name__}: {e}")
        ok = False
    print(f"{mode.upper()} TEST: {'OK' if ok else 'FAILED'}")
    sys.exit(0 if ok else 1)
