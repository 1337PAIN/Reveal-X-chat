"""Ask OSV whether any vendored JavaScript release has a published advisory.

    python scripts/check_vendored_advisories.py

Reads app/static/vendor/vendor.json and, for each library, POSTs
{"package": {"name": ..., "ecosystem": "npm"}, "version": ...} to
https://api.osv.dev/v1/query. Exit codes:

    0  no advisory for any release
    1  at least one advisory (each is printed with its id and summary)
    2  OSV could not be asked, or answered with something unreadable

pip-audit covers the Python side; nothing covered these files, which is how
DOMPurify sat at 3.0.6 with twenty-three advisories against it. CI runs this
on every push and weekly, because an advisory can be published against a
release that has not changed.

Only the standard library is used, so the check needs no install step.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'vendor' / 'vendor.json'
OSV_QUERY = 'https://api.osv.dev/v1/query'


def query(name, version):
    body = json.dumps({'package': {'name': name, 'ecosystem': 'npm'}, 'version': version})
    request = urllib.request.Request(
        OSV_QUERY, data=body.encode(), method='POST',
        headers={'Content-Type': 'application/json', 'User-Agent': 'reveal-x-advisory-check'})
    with urllib.request.urlopen(request, timeout=30) as reply:
        answer = json.loads(reply.read())
    vulns = answer.get('vulns', [])
    if not isinstance(vulns, list):
        raise ValueError(f'unexpected OSV reply for {name}: {answer!r}')
    return vulns


def main(argv):
    libraries = json.loads(MANIFEST.read_text(encoding='utf-8'))['libraries']
    found = 0
    for lib in libraries:
        name, version = lib['npm'], lib['version']
        try:
            vulns = query(name, version)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f'ERROR: could not check {name}@{version}: {exc}')
            return 2
        if not vulns:
            print(f'ok    {name}@{version}: no known advisories')
            continue
        found += len(vulns)
        for vuln in vulns:
            print(f'VULN  {name}@{version}: {vuln.get("id", "?")} {vuln.get("summary", "")}'.rstrip())
    if found:
        print(f'{found} advisory(ies) against vendored releases. Upgrade the file, then its '
              'version, source and sha256 in vendor.json.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
