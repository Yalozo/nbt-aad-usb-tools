"""Optional read-only smoke test against the official GitHub release assets."""
import json
from pathlib import Path
import sys
from urllib.request import urlopen
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nbt_aad_pc.releases import list_releases, download_package, cached_package
cache = Path(sys.argv[1])
releases = list_releases()
assert releases
package = download_package(releases[0], cache, print)
assert len(package.files()) in (6, 7)
has_helper = any(a['name'] == 'pf_aa' for a in releases[0]['assets'])
assert ('pf_aa' in package.files()) == has_helper
assert cached_package(cache).path == package.path
print('Verified official package:', package.label)
for name in ('paramiko', 'pyinstaller'):
    with urlopen('https://pypi.org/pypi/' + name + '/json', timeout=30) as response:
        metadata = json.load(response)
    required = '4.0.0' if name == 'paramiko' else '6.22.0'
    assert required in metadata['releases']
    print(name, required, 'exists; latest:', metadata['info']['version'])
