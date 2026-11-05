#!/bin/bash
# Current cryptography no longer publishes Intel Mac wheels. Build it against
# a private static OpenSSL, rather than bundling the runner's macOS 15 Homebrew.
set -euo pipefail
MAC_DIR=$(cd "$(dirname "$0")" && pwd)
export MACOSX_DEPLOYMENT_TARGET=13.0
for tool in perl clang make cargo; do
    command -v "$tool" >/dev/null || { echo "Missing build tool: $tool" >&2; exit 1; }
done
mkdir -p "$MAC_DIR/build"
cd "$MAC_DIR/build"
OPENSSL_VERSION=3.5.9
OPENSSL_ARCHIVE="openssl-$OPENSSL_VERSION.tar.gz"
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
    "https://github.com/openssl/openssl/releases/download/openssl-$OPENSSL_VERSION/$OPENSSL_ARCHIVE" \
    --output "$OPENSSL_ARCHIVE"
echo "603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a  $OPENSSL_ARCHIVE" | shasum -a 256 -c -
tar -xzf "$OPENSSL_ARCHIVE"
cd "openssl-$OPENSSL_VERSION"
./Configure darwin64-x86_64-cc no-shared no-tests no-module \
    --prefix="$MAC_DIR/build/openssl-prefix" --openssldir="$MAC_DIR/build/openssl-prefix/ssl" \
    -mmacosx-version-min=13.0
make -j "$(sysctl -n hw.ncpu)"
make install_sw
cp LICENSE.txt "$MAC_DIR/build/openssl-prefix/OPENSSL-LICENSE.txt"
