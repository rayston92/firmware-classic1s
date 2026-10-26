"""Keep the wallet running and drive it step by step.

  python ctl.py serve <elf> <sysfs|-> <outdir> [state.img]  # start the wallet ('-': built-in fake sysfs)
  python ctl.py <outdir> save <path>           # copy the wallet's storage file
  python ctl.py <outdir> right|left|up|down [n] # press a key n times, save a screenshot
  python ctl.py <outdir> shot <label>          # just save a screenshot
  python ctl.py <outdir> quit
Every reply is the path of the screenshot taken after the action.
"""
import os, socket, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def serve(elf, sysfs, out, state=None):
    os.environ["E2E_OUT"] = os.path.dirname(out)
    from harness import Wallet
    # RNGSHIM_* variables in the controller's environment are passed to the wallet.
    extra = {k: v for k, v in os.environ.items() if k.startswith("RNGSHIM_")}
    w = Wallet(elf, None if sysfs == "-" else sysfs, os.path.basename(out), state or None, extra)
    sock_path = os.path.join(out, "ctl.sock")
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(sock_path)
    s.listen(4)
    time.sleep(1.5)
    w.shot("start")
    while True:
        conn, _ = s.accept()
        args = conn.recv(1024).decode().split()
        if not args:
            conn.close(); continue
        if args[0] == "quit":
            conn.sendall(w.stop()[-3000:].encode()); conn.close(); break
        if args[0] == "save":
            w.save_state(args[1]); path = args[1]
        elif args[0] == "shot":
            path = w.shot(args[1] if len(args) > 1 else "shot")
        else:
            n = int(args[1]) if len(args) > 1 else 1
            hold = float(args[2]) if len(args) > 2 else 0.12
            for _ in range(n):
                w.press(args[0], hold)
                time.sleep(0.45)
            time.sleep(0.4)
            path = w.shot(f"{args[0]}{n if n > 1 else ''}")
        try:
            conn.sendall(path.encode())
        except OSError:
            pass  # the caller went away; keep the wallet running
        conn.close()


def send(out, *args):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(os.path.join(out, "ctl.sock"))
    c.sendall(" ".join(args).encode())
    data = b""
    while True:
        chunk = c.recv(65536)
        if not chunk:
            break
        data += chunk
    return data.decode()


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        serve(*sys.argv[2:6])
    else:
        print(send(sys.argv[1], *sys.argv[2:]))
