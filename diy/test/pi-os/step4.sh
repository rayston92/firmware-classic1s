#!/bin/bash
# step4.sh: run the guide's step 4 ("Build the firmware") word for word, as the
# user "you" inside the Raspberry Pi OS image (chroot, ARM64 via qemu-user).
# Run as root on a Linux machine with qemu-user-static (binfmt with the C flag,
# so sudo works in the chroot), with the image's root partition mounted at
# $P/root. PROXY and PROXY_CA are only for machines that reach the internet
# through a proxy (the run on 29 October 2026 did); leave them unset otherwise.
set -u
P=${P:-$PWD}
R=$P/root
PROXY=${PROXY:-}

[ -n "${PROXY_CA:-}" ] && cp "$PROXY_CA" $R/usr/local/share/ca-certificates/proxy.crt
cp /etc/resolv.conf $R/etc/resolv.conf 2>/dev/null || true
for d in proc sys dev dev/pts; do mountpoint -q $R/$d || mount --bind /$d $R/$d; done

[ -n "$PROXY" ] && chroot $R /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root bash -c "update-ca-certificates >/dev/null 2>&1; echo 'Acquire::http::Proxy \"$PROXY\"; Acquire::https::Proxy \"$PROXY\";' > /etc/apt/apt.conf.d/99proxy"
chroot $R id you

# The guide's commands, unchanged, run as the user created by Imager's settings.
unshare --uts sh -c 'hostname diywallet; exec "$@"' sh chroot $R /usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/home/you USER=you LANG=C.UTF-8 \
  http_proxy=$PROXY https_proxy=$PROXY no_proxy=localhost,127.0.0.1 \
  PIP_CERT=/etc/ssl/certs/ca-certificates.crt REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
  setpriv --reuid=you --regid=you --init-groups bash -c '
set -e
cd ~
echo "== Install the build tools"
sudo -E apt update
sudo -E apt install -y git build-essential python3-venv protobuf-compiler libprotobuf-dev
echo "== Download the source"
git clone --depth 1 --branch diy https://github.com/rayston92/firmware-classic1s.git ~/wallet
cd ~/wallet
git submodule update --init --recursive --depth 1
git log -1 --format=%H
echo "== Set up Python"
python3 -m venv ~/wallet-venv
. ~/wallet-venv/bin/activate
pip install "nanopb==0.4.*" protobuf mako munch click requests typing_extensions
echo "== Compile"
cd ~/wallet/legacy
time EMULATOR=1 DIY_HAT=1 DEBUG_LINK=0 ./script/cibuild
echo "== Checkpoint"
ls -lh firmware/onekey_emu.elf
'
echo "step4 exit: $?"
