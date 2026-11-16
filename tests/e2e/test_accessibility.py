"""axe-core over the real pages, at desktop and phone widths.

docs/ACCESSIBILITY.md used to record a manual axe run. This makes it a gate:
login, the chat with a conversation open, settings and /lab, each at 1440 px
and 375 px, in both themes (/lab has one), with axe's AAA contrast rule on as
well as its defaults -- the project claims AAA contrast -- and any violation
fails the build. axe-core is pinned in
tests/e2e/package.json and injected from node_modules, so the scan makes no
third-party request.
"""

import json
from pathlib import Path

import pytest

from e2e_helpers import ALICE, BOB, new_page, shot, sign_in

pytestmark = pytest.mark.e2e

AXE = Path(__file__).resolve().parent / 'node_modules' / 'axe-core' / 'axe.min.js'
VENDOR_JSON = Path(__file__).resolve().parents[2] / 'app' / 'static' / 'vendor' / 'vendor.json'
WIDTHS = {'desktop': (1440, 900), 'phone': (375, 812)}
THEMES = ('dark', 'light')

# Transitions frozen before measuring: mid-fade, axe measures the old theme's
# surface under the new theme's text and reports contrast that never settles
# on screen (see docs/ACCESSIBILITY.md).
FREEZE = '*, *::before, *::after { transition: none !important; animation: none !important; }'


def axe_violations(page):
    if not AXE.is_file():
        pytest.fail('axe-core is not installed: run `npm ci` in tests/e2e')
    # Evaluated through DevTools rather than added as a <script>: the app's
    # CSP rightly refuses an injected inline script, and loosening it for the
    # scan (bypass_csp) would be testing a different page.
    page.evaluate(AXE.read_text(encoding='utf-8') + '\n;0')
    result = page.evaluate('''() => axe.run(document, {
        resultTypes: ["violations"],
        rules: { "color-contrast-enhanced": { enabled: true } },
    })''')
    return [
        f"{v['id']} ({v['impact']}): {v['help']} -- "
        + '; '.join(' '.join(node['target']) for node in v['nodes'][:5])
        for v in result['violations']
    ]


def check(page, name):
    # Let entrance transitions finish, so contrast is measured on the final
    # colours rather than half-way through a fade.
    page.add_style_tag(content=FREEZE)
    page.wait_for_timeout(800)
    shot(page, f'axe-{name}')
    violations = axe_violations(page)
    assert not violations, f'{name}:\n' + '\n'.join(violations)


def themed(page, theme):
    """Start the page in this theme, the way a returning user's choice does."""
    page.context.add_init_script(f"localStorage.setItem('revealx_theme', '{theme}')")
    return page


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('width', WIDTHS, ids=list(WIDTHS))
def test_login(browser, server, width, theme):
    page = themed(new_page(browser, *WIDTHS[width]), theme)
    try:
        page.goto(server.url + '/')
        page.wait_for_selector('#usernameInput')
        check(page, f'login-{width}-{theme}')
    finally:
        page.context.close()


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('width', WIDTHS, ids=list(WIDTHS))
def test_chat(browser, server, width, theme):
    bob = sign_in(new_page(browser), server, BOB)
    page = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, ALICE)
    try:
        if width == 'phone':
            # The contact list is a drawer on a phone.
            page.click('#sidebarToggle')
        page.locator('#usersList button', has_text=BOB[0]).click()
        page.fill('#messageInput', 'checked by axe')
        page.click('#sendBtn')
        page.locator('#messagesContainer .message', has_text='checked by axe').wait_for()
        check(page, f'chat-{width}-{theme}')
    finally:
        page.context.close()
        bob.context.close()


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('width', WIDTHS, ids=list(WIDTHS))
def test_settings(browser, server, width, theme):
    page = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, ALICE)
    try:
        if width == 'phone':
            page.click('#sidebarToggle')
        page.click('#settingsBtn')
        page.wait_for_selector('#settingsModal:not(.hidden)')
        check(page, f'settings-{width}-{theme}')
    finally:
        page.context.close()


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('width', WIDTHS, ids=list(WIDTHS))
def test_image_share(browser, server, width, theme, tmp_path):
    """The send dialog, and the recipient's share card with its evidence open."""
    from test_flows import png_bytes

    alice = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, ALICE)
    bob = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, BOB)
    try:
        for page, peer in ((alice, BOB[0]), (bob, ALICE[0])):
            if width == 'phone':
                page.click('#sidebarToggle')
            page.locator('#usersList button', has_text=peer).click()
        picture = tmp_path / 'axe.png'
        picture.write_bytes(png_bytes())
        alice.set_input_files('#imageInput', str(picture))
        alice.wait_for_selector('#imagePreviewModal:not(.hidden)')
        check(alice, f'share-preview-{width}-{theme}')
        alice.click('#confirmImageBtn')

        share = bob.locator('#messagesContainer .message:has([data-role="tamper-panel"])').last
        share.locator('text=verified intact').wait_for(timeout=30000)
        share.locator('.tamper-details summary').click()
        check(bob, f'share-received-{width}-{theme}')
    finally:
        alice.context.close()
        bob.context.close()


@pytest.mark.parametrize('width', WIDTHS, ids=list(WIDTHS))
def test_lab(browser, server, width):
    page = new_page(browser, *WIDTHS[width])
    try:
        page.goto(server.url + '/lab')
        page.wait_for_load_state('networkidle')
        check(page, f'lab-{width}')
    finally:
        page.context.close()


def test_the_real_dompurify_neutralises_xss(browser, server):
    """The unit tests stub DOMPurify (Node has no DOM). This runs the vendored
    release in the page the app serves it to."""
    page = sign_in(new_page(browser), server, ALICE)
    try:
        expected = next(lib['version'] for lib in json.loads(VENDOR_JSON.read_text())['libraries']
                        if lib['npm'] == 'dompurify')
        assert page.evaluate('() => DOMPurify.version') == expected
        results = page.evaluate('''() => {
            const vectors = [
                '<script>alert(1)<\\/script>hi',
                '<img src=x onerror=alert(1)>',
                '<svg/onload=alert(1)>',
                '<a href="javascript:alert(1)">x</a>',
                '<iframe src="javascript:alert(1)"></iframe>',
                '<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>',
                '<svg><style><img src=x onerror=alert(1)></style></svg>',
            ];
            return vectors.map((v) => window.revealxSanitize.cleanHTML(v));
        }''')
        for cleaned in results:
            for bad in ('onerror', 'onload', 'javascript:', '<script', '<iframe'):
                assert bad not in cleaned.lower(), cleaned
        kept = page.evaluate("() => window.revealxSanitize.cleanHTML('<b>bold</b> <i>it</i>')")
        assert kept == '<b>bold</b> <i>it</i>'
    finally:
        page.context.close()
