"""App code reads the clock through app.timeutil.utcnow, never datetime.utcnow.

datetime.utcnow() is deprecated since Python 3.12 and returns a naive value
that only *means* UTC. pytest.ini used to ignore every DeprecationWarning, so
the warning for it -- and any other deprecation in app code -- never showed.
Now app code is run with its deprecations turned into errors, and stored
timestamps keep their exact format: naive ISO 8601, so existing rows and the
string comparisons made against them are unchanged.
"""

import re
import warnings
from datetime import datetime, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / 'app'


def test_no_app_module_calls_utcnow():
    offenders = [str(p.relative_to(APP)) for p in APP.rglob('*.py')
                 if re.search(r'datetime\.utcnow\(', p.read_text(encoding='utf-8'))
                 and p.name != 'timeutil.py']
    assert not offenders, offenders


def test_the_helper_keeps_the_stored_format():
    from app.timeutil import utcnow
    now = utcnow()
    assert now.tzinfo is None, 'stored timestamps are naive ISO strings'
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?', now.isoformat())
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        legacy = datetime.utcnow()
    assert abs(now - legacy) < timedelta(seconds=5), 'not UTC'


def test_pytest_turns_app_deprecations_into_errors():
    ini = (APP.parent / 'pytest.ini').read_text(encoding='utf-8')
    lines = [l.strip() for l in ini.splitlines()]
    assert 'error::DeprecationWarning:app' in ' '.join(lines)
    # It must come after the blanket ignore, so it takes precedence.
    assert lines.index('ignore::DeprecationWarning') < \
        next(i for i, l in enumerate(lines) if l.startswith('error::DeprecationWarning:app'))
