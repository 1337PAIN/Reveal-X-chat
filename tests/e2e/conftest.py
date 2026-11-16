"""Fixtures for the browser end-to-end tests (pytest -m e2e).

Everything here drives the real app in a real Chromium: a server process on a
free port with its own temporary SQLite database, and one browser context per
person, so two people really are two browsers with separate IndexedDB key
stores. pytest.ini deselects these by default, so the normal suite needs no
browser; Playwright is imported only when a fixture is used.
"""

import pytest

from e2e_helpers import ALICE, BOB, Server, new_page, shot, sign_in


@pytest.fixture
def server(tmp_path_factory):
    # One server and database per test. Each test's browsers generate new key
    # pairs, and a server remembering the last test's keys would hand Alice a
    # stale key for Bob -- which the app rightly reports as a changed safety
    # number. A fresh start costs a few seconds and removes that coupling.
    srv = Server(tmp_path_factory.mktemp('e2e-server'))
    srv.start()
    yield srv
    srv.stop()
    srv.log.close()


@pytest.fixture(scope='session')
def playwright():
    """One Playwright for the whole run: its sync API refuses a second
    instance in the same process, so every browser is launched from this."""
    sync_api = pytest.importorskip('playwright.sync_api')
    with sync_api.sync_playwright() as instance:
        yield instance


@pytest.fixture(scope='session')
def browser(playwright):
    chromium = playwright.chromium.launch()
    yield chromium
    chromium.close()


@pytest.fixture(scope='session')
def mic_browser(playwright):
    """Chromium with a fake microphone (it plays a tone) and no permission
    prompt, for recording real voice notes. The plain `browser` has no audio
    input at all, which the no-microphone test depends on."""
    chromium = playwright.chromium.launch(args=[
        '--use-fake-device-for-media-stream',
        '--use-fake-ui-for-media-stream',
    ])
    yield chromium
    chromium.close()


@pytest.fixture
def pair(browser, server, request):
    """Alice and Bob, signed in, in separate browser contexts."""
    alice = sign_in(new_page(browser), server, ALICE)
    bob = sign_in(new_page(browser), server, BOB)
    yield alice, bob
    test = request.node.name.replace('[', '-').replace(']', '')
    for who, page in (('alice', alice), ('bob', bob)):
        try:
            shot(page, f'{test}-{who}')
        except Exception:
            pass
        page.context.close()
