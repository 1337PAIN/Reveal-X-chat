"""Diagnostics go through logging; administrative actions go to a JSON audit log.

Everything used to be print(): no levels, no timestamps on most lines, no way
to silence the per-connection chatter in production or to route the audit
trail anywhere but the same stream. The audit line was "[audit] ... key=value"
text, where a value containing a space or a newline could make one entry read
as two.
"""

import json
import re
from pathlib import Path

import pytest

from app import app as flask_app, socketio

APP = Path(__file__).resolve().parent.parent / 'app'


def audit_entries(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('{') and '"audit"' in line:
            out.append(json.loads(line))
    return out


def test_app_code_does_not_print():
    offenders = []
    for path in APP.rglob('*.py'):
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if re.match(r'\s*print\(', line):
                offenders.append(f'{path.relative_to(APP)}:{number}')
    assert not offenders, offenders


def test_the_audit_log_is_one_json_object_per_line(store, capsys):
    from app.log import audit
    audit(actor='root', action='set_state', target='subject', detail='state=disabled')
    entries = audit_entries(capsys.readouterr().out)
    assert len(entries) == 1
    entry = entries[0]
    assert entry['type'] == 'audit'
    assert entry['actor'] == 'root' and entry['action'] == 'set_state'
    assert entry['target'] == 'subject' and entry['detail'] == 'state=disabled'
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', entry['ts'])


def test_a_hostile_value_cannot_forge_a_second_entry(store, capsys):
    from app.log import audit
    audit(actor='root', action='rename', target='x\n{"type": "audit", "action": "forged"}')
    text = capsys.readouterr().out
    entries = audit_entries(text)
    assert len(entries) == 1
    assert 'forged' not in [e['action'] for e in entries]


def test_the_app_logger_has_levels_and_timestamps(capsys):
    from app.log import log
    log.warning('something to look at')
    err = capsys.readouterr().err
    assert re.search(r'\d{4}-\d\d-\d\d.*WARNING.*revealx.*something to look at', err)
