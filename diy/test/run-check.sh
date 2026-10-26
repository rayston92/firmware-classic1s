#!/bin/bash
# Start the wallet with fresh storage (or STATE=<storage file>), run one check
# against it, then stop the wallet. Runs in its own network namespace, so it
# needs no root and never touches another wallet on 127.0.0.1:54935.
#
#   ./run-check.sh <wallet program> <output dir> <check.py> [check args...]
#
# Examples (activate the venv from README.md first):
#   ./run-check.sh test-build.elf /tmp/psbt psbt_check.py
#   STATE=wallet.img ./run-check.sh wallet.elf /tmp/flow unlock_then.py 1234 wallet_flow.py "word1 ... word12"
set -e
if [ -z "$IN_NETNS" ]; then
  exec env IN_NETNS=1 unshare -rn "$0" "$@"
fi
HERE=$(cd "$(dirname "$0")" && pwd)
ELF=$(realpath "$1"); OUT=$2; CHECK=$3; shift 3
python3 - <<'PY'
import fcntl, socket, struct  # bring up the loopback interface in the new namespace
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
f = struct.unpack("16sH14x", fcntl.ioctl(s, 0x8913, struct.pack("16sH14x", b"lo", 0)))[1]
fcntl.ioctl(s, 0x8914, struct.pack("16sH14x", b"lo", f | 1))
PY
rm -rf "$OUT"; mkdir -p "$OUT"
python -u "$HERE/ctl.py" serve "$ELF" - "$OUT/run" ${STATE:+"$(realpath "$STATE")"} > "$OUT/serve.log" 2>&1 &
for _ in $(seq 1 40); do [ -S "$OUT/run/ctl.sock" ] && break; sleep 0.5; done
sleep 2
set +e
python -u "$HERE/$CHECK" "$OUT/run" "$@"
RC=$?
python "$HERE/ctl.py" "$OUT/run" quit > "$OUT/wallet-tail.log" 2>&1
wait
exit $RC
