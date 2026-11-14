"""scripts/check_vendored_advisories.py asks OSV the right question and fails
on any answer.

The network is replaced here: the suite makes no outside requests, and what
is under test is the script's handling of OSV's replies, not OSV. The real
query runs in CI (the vendored-advisories job).
"""

import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    'check_vendored_advisories', ROOT / 'scripts' / 'check_vendored_advisories.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

MANIFEST = json.loads((ROOT / 'app' / 'static' / 'vendor' / 'vendor.json').read_text(encoding='utf-8'))


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_osv(answers, seen):
    def urlopen(request, timeout=None):
        assert request.full_url == 'https://api.osv.dev/v1/query'
        assert request.get_method() == 'POST'
        body = json.loads(request.data)
        seen.append(body)
        return _Reply(json.dumps(answers.get(body['package']['name'], {})).encode())
    return urlopen


def test_each_npm_entry_is_queried_by_name_ecosystem_and_version(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(checker.urllib.request, 'urlopen', _fake_osv({}, seen))
    assert checker.main([]) == 0
    expected = [{'package': {'name': lib['npm'], 'ecosystem': 'npm'}, 'version': lib['version']}
                for lib in MANIFEST['libraries']]
    assert seen == expected
    assert 'no known advisories' in capsys.readouterr().out


def test_any_advisory_fails_the_check_and_is_named(monkeypatch, capsys):
    answers = {'dompurify': {'vulns': [{'id': 'GHSA-xxxx-yyyy-zzzz', 'summary': 'mXSS bypass'}]}}
    monkeypatch.setattr(checker.urllib.request, 'urlopen', _fake_osv(answers, []))
    assert checker.main([]) == 1
    out = capsys.readouterr().out
    assert 'GHSA-xxxx-yyyy-zzzz' in out and 'dompurify' in out and 'mXSS bypass' in out


def test_an_unreachable_osv_fails_rather_than_passing(monkeypatch):
    """No answer is not the same as no advisories."""
    def down(request, timeout=None):
        raise checker.urllib.error.URLError('offline')
    monkeypatch.setattr(checker.urllib.request, 'urlopen', down)
    assert checker.main([]) == 2


@pytest.mark.parametrize('reply', [b'not json', b'{"vulns": "nope"}'])
def test_a_malformed_reply_fails(monkeypatch, reply):
    monkeypatch.setattr(checker.urllib.request, 'urlopen', lambda r, timeout=None: _Reply(reply))
    assert checker.main([]) == 2
