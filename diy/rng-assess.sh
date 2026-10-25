#!/bin/sh
# Collect raw output of the Raspberry Pi's hardware random number generator
# for a NIST SP 800-90B entropy assessment. For people who want to measure the
# generator themselves; the wallet does not need this.
#
#   sudo ./diy/rng-assess.sh [file]      writes 1,000,000 raw bytes (default hwrng-raw.bin)
#
# Then, on any Linux computer with build tools:
#   git clone --depth 1 https://github.com/usnistgov/SP800-90B_EntropyAssessment
#   cd SP800-90B_EntropyAssessment/cpp && make non_iid
#   ./ea_non_iid -v /path/to/hwrng-raw.bin 8
# The min-entropy it reports should be well above 4 bits per byte: the wallet's
# health tests (legacy/emulator/diy/rand_hw.c) assume at least that much.
#
# License: GNU GPL v3 or later.
set -eu
OUT=${1:-hwrng-raw.bin}
CHECK=/opt/diywallet/hwrng-check
[ -x "$CHECK" ] || CHECK="$(dirname "$0")/hwrng-check"
if [ -x "$CHECK" ]; then
  "$CHECK"                       # same generator and settings the wallet uses
else
  echo "hwrng-check not found; run diy/install.sh first." >&2
  exit 1
fi
# The wallet discards the first 1 MiB after start; do the same here.
dd if=/dev/hwrng of=/dev/null bs=4096 count=256 iflag=fullblock status=none
dd if=/dev/hwrng of="$OUT" bs=1000 count=1000 iflag=fullblock status=none
echo "Wrote $(wc -c < "$OUT") raw bytes to $OUT."
