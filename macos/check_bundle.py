"""Check architecture and minimum OS of the actual bundled Mach-O files."""
from pathlib import Path
import platform
import re
import subprocess
import sys

bundle = Path(sys.argv[1])
executable = bundle / 'Contents/MacOS/NBT-AAD-PC'
architecture = subprocess.check_output(['lipo', '-archs', str(executable)], text=True).strip()
assert architecture == platform.machine(), architecture
magic = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
         b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
count, maximum = 0, (0, 0, 0)
for file in bundle.rglob('*'):
    if file.is_symlink() or not file.is_file():
        continue
    with file.open('rb') as source:
        if source.read(4) not in magic:
            continue
    output = subprocess.check_output(['otool', '-l', str(file)], text=True)
    versions = re.findall(r'cmd LC_(?:BUILD_VERSION|VERSION_MIN_MACOSX)\n.*?\n\s+(?:minos|version) ([0-9.]+)',
                          output, re.S)
    if not versions:
        raise RuntimeError('Missing macOS deployment target: ' + str(file))
    for value in versions:
        version = tuple((list(map(int, value.split('.'))) + [0, 0])[:3])
        if version > (13, 0, 0):
            raise RuntimeError(str(file) + ' requires macOS ' + value + ', expected <=13.0')
        maximum = max(maximum, version)
    count += 1
print('Verified native architecture:', architecture, '; Mach-O files:', count,
      '; maximum minimum macOS:', '.'.join(map(str, maximum)))
