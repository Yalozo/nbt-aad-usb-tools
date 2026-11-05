#!/bin/bash
set -euo pipefail
MAC_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_DIR=$(cd "$MAC_DIR/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
[ "$(uname -s)" = Darwin ] || { echo 'Build on macOS with a GUI-capable Python 3.12.' >&2; exit 1; }
cd "$MAC_DIR"
"$PYTHON_BIN" -m venv .venv
BUILD_PYTHON="$MAC_DIR/.venv/bin/python"
if [ "$(uname -m)" = x86_64 ]; then
    bash build_crypto.sh
    export OPENSSL_DIR="$MAC_DIR/build/openssl-prefix"
    export OPENSSL_STATIC=1
    export OPENSSL_NO_VENDOR=1
    export MACOSX_DEPLOYMENT_TARGET=13.0
    "$BUILD_PYTHON" -m pip install --no-cache-dir --no-binary=cryptography -r requirements-build.txt
else
    "$BUILD_PYTHON" -m pip install --only-binary=cryptography -r requirements-build.txt
fi
cd "$REPO_DIR/windows"
"$BUILD_PYTHON" -m unittest discover -s tests -v
cd "$MAC_DIR"
"$BUILD_PYTHON" -m PyInstaller --noconfirm --clean --distpath dist --workpath build nbt-aad.spec
"$BUILD_PYTHON" check_bundle.py dist/NBT-AAD-PC.app
codesign --verify --deep --strict --verbose=2 dist/NBT-AAD-PC.app
# Exercise the bundled app with temporary settings, never a real HU connection.
"$BUILD_PYTHON" -c 'import subprocess; subprocess.run(["dist/NBT-AAD-PC.app/Contents/MacOS/NBT-AAD-PC", "--self-test"], check=True, timeout=60)'
cp README.md dist/README.md
cp "$REPO_DIR/LICENSE" dist/LICENSE.txt
"$BUILD_PYTHON" "$REPO_DIR/windows/collect_licenses.py"
if [ -f "$MAC_DIR/build/openssl-prefix/OPENSSL-LICENSE.txt" ]; then
    cp "$MAC_DIR/build/openssl-prefix/OPENSSL-LICENSE.txt" dist/
fi
"$BUILD_PYTHON" -m pip freeze > dist/dependencies.txt
MAC_ARCH=$(uname -m)
PACKAGE_DIR="$MAC_DIR/package"
mkdir -p "$PACKAGE_DIR"
# ditto preserves framework symlinks and bundle permissions inside the ZIP.
ditto -c -k --sequesterRsrc --keepParent dist/NBT-AAD-PC.app "$PACKAGE_DIR/NBT-AAD-PC-macOS-$MAC_ARCH.zip"
cp dist/README.md dist/LICENSE.txt dist/THIRD-PARTY-NOTICES.txt dist/dependencies.txt "$PACKAGE_DIR/"
if [ -f dist/OPENSSL-LICENSE.txt ]; then cp dist/OPENSSL-LICENSE.txt "$PACKAGE_DIR/"; fi
cd "$PACKAGE_DIR"
shasum -a 256 "NBT-AAD-PC-macOS-$MAC_ARCH.zip" > SHA256.txt
echo "Ready: $PACKAGE_DIR/NBT-AAD-PC-macOS-$MAC_ARCH.zip"
