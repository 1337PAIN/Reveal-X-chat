"""Every vendored file is the release vendor.json says it is.

The vendored libraries are the part of the front end nobody reads: DOMPurify
sat at 3.0.6 for the life of the project, with twenty-three published
advisories against it, because nothing recorded which release it was or
asked whether that release was still safe. vendor.json is that record, and
scripts/check_vendored_advisories.py asks OSV about it. These tests keep the
record honest: a file swapped without updating vendor.json, or a version
typed into vendor.json that the file does not carry, fails here.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'app' / 'static' / 'vendor'
MANIFEST = json.loads((VENDOR / 'vendor.json').read_text(encoding='utf-8'))
LIBRARIES = MANIFEST['libraries']

# Where a library stamps its version into a file, this is how to read it.
# Fonts and the wasm binary carry none, so they are checked by hash alone.
BANNERS = {
    'dompurify': r'@license DOMPurify (\d+\.\d+\.\d+)',
    'socket.io-client': r'Socket\.IO v(\d+\.\d+\.\d+)',
    'onnxruntime-web': r'ONNX Runtime Web v(\d+\.\d+\.\d+)',
    '@fontsource/inter': r'@fontsource/inter (\d+\.\d+\.\d+)',
}


def _files():
    for lib in LIBRARIES:
        for entry in lib['files']:
            yield lib['npm'], entry


FILES = list(_files())


def test_the_four_libraries_are_listed():
    assert {lib['npm'] for lib in LIBRARIES} == set(BANNERS)
    for lib in LIBRARIES:
        assert re.fullmatch(r'\d+\.\d+\.\d+', lib['version']), lib
        assert lib['source'].startswith('https://registry.npmjs.org/'), lib
        assert lib['source'].endswith(f"-{lib['version']}.tgz"), lib


@pytest.mark.parametrize('npm,entry', FILES, ids=[e['path'] for _, e in FILES])
def test_each_listed_file_exists_and_matches_its_hash(npm, entry):
    path = VENDOR / entry['path']
    assert path.is_file(), f'{npm}: {entry["path"]} is listed but missing'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == entry['sha256'], (
        f'{entry["path"]} has changed. If it was upgraded on purpose, update '
        'its version and sha256 in vendor.json; .gitattributes keeps vendor/ '
        'byte-exact, so a CRLF checkout is not the cause.')


def test_every_vendored_file_is_listed():
    """Nothing reaches vendor/ without a version and a source."""
    listed = {entry['path'] for _, entry in FILES}
    on_disk = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob('*')
               if p.is_file() and p.name != 'vendor.json'}
    assert on_disk - listed == set(), f'vendored but not in vendor.json: {on_disk - listed}'


@pytest.mark.parametrize('lib', LIBRARIES, ids=[lib['npm'] for lib in LIBRARIES])
def test_the_version_matches_the_files_banner(lib):
    pattern = BANNERS[lib['npm']]
    found = set()
    for entry in lib['files']:
        head = (VENDOR / entry['path']).read_bytes()[:400]
        match = re.search(pattern.encode(), head)
        if match:
            found.add(match.group(1).decode())
    assert found, f"{lib['npm']}: no file carries a version banner"
    assert found == {lib['version']}, f"{lib['npm']}: vendor.json says {lib['version']}, files say {found}"


def test_dompurify_is_at_least_the_first_release_without_known_advisories():
    lib = next(lib for lib in LIBRARIES if lib['npm'] == 'dompurify')
    major, minor, patch = (int(x) for x in lib['version'].split('.'))
    assert major == 3 and (minor, patch) >= (4, 13), lib['version']
