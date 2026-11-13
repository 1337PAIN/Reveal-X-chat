"""The numbers in the docs are the numbers in the code.

The README said logins were limited to 20 per address when the code said 50,
API.md counted 42 Socket.IO events after two more had been added, and the
test counts fell behind by dozens. Each was right once and then drifted,
because nothing connected the sentence to the constant. These do.
"""

import re
from pathlib import Path

import pytest

from app import app as flask_app, socketio
from app import models, routes

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / 'README.md').read_text(encoding='utf-8')
API = (ROOT / 'docs' / 'API.md').read_text(encoding='utf-8')
SECURITY = (ROOT / 'docs' / 'SECURITY.md').read_text(encoding='utf-8')


def number(pattern, text, where):
    # Sentences wrap in the Markdown source, so a space in a pattern matches
    # any run of whitespace, line breaks included.
    match = re.search(pattern.replace(' ', r'\s+'), text)
    assert match, f'{where}: no sentence matching {pattern!r}'
    return int(match.group(1))


def http_routes():
    return [rule for rule in flask_app.url_map.iter_rules() if rule.endpoint != 'static']


def socket_events():
    return list(socketio.server.handlers['/'])


def test_route_and_event_counts():
    routes_n, events_n = len(http_routes()), len(socket_events())
    for text, where in ((API, 'API.md'), (README, 'README.md')):
        assert number(r'(\d+) HTTP routes', text, where) == routes_n, where
        assert number(r'(\d+) Socket\.IO events', text, where) == events_n, where


def test_every_socket_event_is_documented_in_api_md():
    missing = [e for e in socket_events() if f'`{e}`' not in API]
    assert not missing, f'API.md does not mention: {missing}'


def test_login_throttle_numbers():
    window = routes.LOGIN_WINDOW_SECONDS // 60
    assert number(r'Per account \| (\d+) per 15 min', README, 'README') == routes.LOGIN_MAX_ATTEMPTS
    assert number(r'Per address \| (\d+) per 15 min', README, 'README') == routes.LOGIN_IP_MAX_ATTEMPTS
    assert window == 15
    for text, where in ((API, 'API.md'), (SECURITY, 'SECURITY.md')):
        assert number(r'(\d+)\s+per account', text, where) == routes.LOGIN_MAX_ATTEMPTS, where
        assert number(r'(\d+)\s+per\s+address', text, where) == routes.LOGIN_IP_MAX_ATTEMPTS, where


def test_lab_and_registration_limits():
    assert number(r'budget of (\d+) requests per address per minute', SECURITY, 'SECURITY.md') \
        == routes.LAB_MAX_REQUESTS
    assert routes.LAB_WINDOW_SECONDS == 60
    assert number(r'capped at (\d+) per address per 10 minutes', SECURITY, 'SECURITY.md') \
        == routes.REGISTER_MAX_REQUESTS
    assert routes.REGISTER_WINDOW_SECONDS == 600


def test_send_limits():
    message, voice, image = (routes.SEND_LIMITS[k] for k in ('message', 'voice', 'image'))
    assert number(r'bucket of (\d+) that refills at one a second', API, 'API.md') == message[0]
    assert message[1] == 1.0
    assert number(r'`send_voice` has (\d+) refilling every 6 seconds', API, 'API.md') == voice[0]
    assert abs(voice[1] - 1 / 6) < 1e-9
    assert number(r'`send_share` (\d+) refilling every 15 seconds', API, 'API.md') == image[0]
    assert abs(image[1] - 1 / 15) < 1e-9


def test_lifetimes():
    assert number(r'MESSAGE_TTL_HOURS = (\d+)', SECURITY, 'SECURITY.md') == models.MESSAGE_TTL_HOURS
    assert number(r'It lasts \*\*(\d+) hours\*\* from sign-in', API, 'API.md') \
        == models.RESUME_TOKEN_TTL_HOURS


def test_share_limits():
    assert number(r'between 8 and (\d+) px a side', API, 'API.md') == routes.MAX_SHARE_SIDE
    assert routes.MIN_SHARE_SIDE == 8
    assert number(r'at most (\d+) MB; the server stores', API, 'API.md') * 1024 * 1024 \
        == routes.MAX_SHARE1_BYTES


def test_the_readme_test_counts_agree_with_each_other():
    counts = re.findall(r'(\d+) server tests', README)
    assert counts and len(set(counts)) == 1, counts
    js = re.findall(r'(\d+) browser tests', README)
    assert js and len(set(js)) == 1, js


def test_the_readme_server_count_is_the_suite(request):
    """Only meaningful on a full run: a -k or a single file collects fewer."""
    config = request.config
    if config.option.keyword or config.option.markexpr or config.args not in ([], ['tests'],
                                                                               [str(ROOT / 'tests')]):
        pytest.skip('not a full run')
    collected = request.session.testscollected
    stated = int(re.search(r'(\d+) server tests', README).group(1))
    assert stated == collected, (
        f'README says {stated} server tests; the suite collects {collected}. '
        'The README counts every collected test, skips included.')


def test_every_file_and_function_security_md_names_exists():
    """SECURITY.md says each control "names the file that implements it". When
    routes.py was split, a dozen of those names pointed at the wrong file."""
    pairs = re.findall(r'`([\w/]+\.py)`,\s+`(\w+)`', SECURITY)
    pairs += [(f, n) for n, f in re.findall(r'`(\w+)` in `([\w/]+\.py)`', SECURITY)]
    assert pairs
    app_dir = ROOT / 'app'
    for filename, name in pairs:
        candidates = [p for p in app_dir.rglob('*.py')
                      if p.as_posix().endswith('/' + filename)]
        assert candidates, f'SECURITY.md names {filename}, which does not exist'
        source = candidates[0].read_text(encoding='utf-8')
        defined = re.search(
            rf'^\s*(?:async\s+)?(?:def|class)\s+{name}\b|^{name}\s*=|^\s*(?:from\s+\S+\s+)?import\b.*\b{name}\b',
            source, re.M)
        assert defined, f'SECURITY.md says {name} is in {filename}; it is not'
