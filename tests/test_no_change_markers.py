"""Shipped code carries no "[NEW]" / "[MODIFIED]" change markers.

They recorded when a line changed, which is what version control is for, and
several had gone stale: "Added debounce attribute for search" sat above an
input that no longer had one, and "Added CSP meta tag as fallback" above a
comment explaining the meta tag was removed. Comments say why code is the
way it is; history stays in git.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = [
    path for path in (ROOT / 'app').rglob('*')
    if path.suffix in {'.py', '.js', '.mjs', '.css', '.html'}
    and 'vendor' not in path.parts
    and path.is_file()
]
MARKER = re.compile(r'\[(NEW|MODIFIED)\]')


def test_there_are_files_to_check():
    assert len(SHIPPED) > 20


def test_no_change_markers():
    found = [
        f'{path.relative_to(ROOT)}:{number}'
        for path in SHIPPED
        for number, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1)
        if MARKER.search(line)
    ]
    assert found == []
