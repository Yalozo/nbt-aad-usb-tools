"""Include the license notices shipped by all installed runtime dependencies."""
from importlib.metadata import distribution, distributions
from pathlib import Path
import re
out = Path('dist/THIRD-PARTY-NOTICES.txt')
notices = []
for dist in sorted(distributions(), key=lambda d: d.metadata['Name'].lower()):
    files = []
    for file in dist.files or []:
        path = str(file)
        if '.dist-info/' in path and re.search(r'(?i)(license|copying|notice)', path):
            located = Path(dist.locate_file(file))
            if located.is_file():
                files.append((path, located.read_text(encoding='utf-8', errors='replace')))
    if files:
        notices.append(dist.metadata['Name'] + ' ' + dist.version + '\n' +
                       '\n'.join(path + '\n' + body for path, body in files))
out.write_text('\n\n' + ('\n\n' + '=' * 72 + '\n\n').join(notices), encoding='utf-8')
