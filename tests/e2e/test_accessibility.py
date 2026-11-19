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

from e2e_helpers import ALICE, BOB, CAROL, new_page, shot, sign_in

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
def test_create_account(browser, server, width, theme):
    """The Create account mode, showing that a name is taken."""
    page = themed(new_page(browser, *WIDTHS[width]), theme)
    try:
        page.goto(server.url + '/')
        page.click('#authModeSignUp')
        page.fill('#usernameInput', ALICE[0])
        page.locator('#usernameStatus', has_text='already taken').wait_for()
        check(page, f'signup-{width}-{theme}')
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
        # Text drawn over text cannot be read: each option's description sits
        # below its label (a stray negative margin once pulled it on top), and
        # the panel reads left-aligned rather than inheriting the centring of
        # the modal around it.
        layout = page.evaluate('''() => [...document.querySelectorAll(
                '#settingsModal .settings-option')].flatMap(option => {
            const label = option.querySelector(':scope > label');
            const hint = option.querySelector(':scope > .settings-hint');
            if (!label || !hint) return [];
            return [{id: label.htmlFor,
                     gap: hint.getBoundingClientRect().top - label.getBoundingClientRect().bottom,
                     align: getComputedStyle(hint).textAlign}];
        })''')
        assert len(layout) == 2, layout
        for row in layout:
            assert row['gap'] >= 0, row
            assert row['align'] in ('left', 'start'), row
        check(page, f'settings-{width}-{theme}')
    finally:
        page.context.close()


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('width', WIDTHS, ids=list(WIDTHS))
def test_reconstruction_panel(browser, server, width, theme, tmp_path):
    from test_flows import send_image

    alice = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, ALICE)
    page = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, BOB)
    try:
        for who, peer in ((alice, BOB[0]), (page, ALICE[0])):
            if width == 'phone':
                who.click('#sidebarToggle')
            who.locator('#usersList button', has_text=peer).click()
        send_image(alice, tmp_path)
        share = page.locator('#messagesContainer .message:has([data-role="tamper-panel"])').last
        share.locator('[data-action="reconstruct"]:not([disabled])').click(timeout=30000)
        page.wait_for_selector('#reconstructionPanel.active')
        # Nothing is cut off while the screen has room for it: on a phone the
        # panel was capped at 45% of the height, which hid the reconstructed
        # image below an inner scroll with half the screen empty beneath it.
        # And the header band reaches the panel's edges instead of sitting
        # inset as a box of its own.
        box = page.evaluate('''() => {
            const panel = document.getElementById('reconstructionPanel');
            const main = panel.parentElement.getBoundingClientRect();
            const p = panel.getBoundingClientRect();
            const h = panel.querySelector('.panel-header').getBoundingClientRect();
            return {hidden: panel.scrollHeight - panel.clientHeight,
                    room: main.bottom - p.bottom,
                    left: h.left - p.left, right: p.right - h.right, top: h.top - p.top};
        }''')
        assert box['hidden'] <= 1 or box['room'] <= 1, box
        for edge in ('left', 'right', 'top'):
            assert box[edge] <= 2, box
        check(page, f'reconstruction-{width}-{theme}')
    finally:
        alice.context.close()
        page.context.close()


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('width', WIDTHS, ids=list(WIDTHS))
def test_reply(browser, server, width, theme):
    """A sent reply's quote, and the reply bar open on the next one."""
    from test_flows import menu_action, message_with, send_text

    alice = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, ALICE)
    bob = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, BOB)
    try:
        for page, peer in ((alice, BOB[0]), (bob, ALICE[0])):
            if width == 'phone':
                page.click('#sidebarToggle')
            page.locator('#usersList button', has_text=peer).click()
        send_text(bob, 'lunch at one?')
        original = message_with(alice, 'lunch at one?')
        original.wait_for()
        menu_action(alice, original, 'reply')
        send_text(alice, 'yes')
        reply = message_with(alice, 'yes')
        reply.locator('.message-reply >> text=lunch at one?').wait_for(timeout=15000)
        menu_action(alice, reply, 'reply')
        alice.wait_for_selector('#replyBar:not(.hidden)')
        check(alice, f'reply-{width}-{theme}')
    finally:
        alice.context.close()
        bob.context.close()


def touch_page(browser, width=360, height=780):
    """A phone: touch, so (pointer: coarse) applies as on a real device."""
    context = browser.new_context(service_workers='block', is_mobile=True, has_touch=True,
                                  viewport={'width': width, 'height': height})
    context.set_default_timeout(15000)
    page = context.new_page()
    page.errors = []
    page.on('pageerror', lambda error: page.errors.append(str(error)))
    return page


def test_a_phone_bubble_footer_stays_on_one_line(browser, server, tmp_path):
    """On a phone the footer (chip, time, Read, "...") was allowed to wrap, and
    each wrapped line was a 44px touch-target tall: a one-word message became
    a tall box with the menu alone on a second row. Every footer is one line
    now, for text and for an image share, the widest."""
    from test_flows import message_with, send_image, send_text

    alice = sign_in(touch_page(browser), server, ALICE)
    bob = sign_in(touch_page(browser), server, BOB)
    try:
        for page, peer in ((alice, BOB[0]), (bob, ALICE[0])):
            page.click('#sidebarToggle')
            page.locator('#usersList button', has_text=peer).click()
        send_text(bob, 'lunch?')
        message_with(alice, 'lunch?').wait_for()
        send_text(alice, 'Hge')
        message_with(bob, 'Hge').wait_for()
        alice.wait_for_timeout(2100)
        send_image(alice, tmp_path)
        bob.locator('#messagesContainer .message:has([data-role="tamper-panel"]) '
                    '[data-role="tamper-panel"] >> text=verified intact').wait_for(timeout=30000)
        for page in (alice, bob):
            rows = page.evaluate('''() => [...document.querySelectorAll('#messagesContainer .message')]
                .map(m => [...m.querySelector('.message-meta').children]
                    .filter(c => c.getBoundingClientRect().width > 0)
                    .map(c => { const r = c.getBoundingClientRect(); return r.top + r.height / 2; }))''')
            assert len(rows) == 3, rows
            for centres in rows:
                assert max(centres) - min(centres) <= 4, rows
            # React lives in the menu on a touch screen, not beside the time.
            assert page.locator('.message-meta .btn-react:visible').count() == 0
    finally:
        alice.context.close()
        bob.context.close()


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


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('width', WIDTHS, ids=list(WIDTHS))
def test_adding_a_contact(browser, server, width, theme):
    """Carol is nobody's contact. Alice adds her by username; Carol sees the
    request, is scanned by axe with it showing, and accepts; then each is in
    the other's list -- and Bob, never added, is not in Carol's."""
    alice = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, ALICE)
    carol = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, CAROL)
    try:
        assert carol.locator('#usersList button').count() == 0
        for page in (alice, carol):
            if width == 'phone':
                page.click('#sidebarToggle')
        alice.fill('#addContactInput', '@' + CAROL[0].upper())
        alice.click('#addContactBtn')
        alice.locator('#contactRequestsList', has_text='waiting for them to accept').wait_for()

        request = carol.locator('#contactRequestsList li', has_text=ALICE[0])
        request.wait_for()
        check(carol, f'contact-request-{width}-{theme}')
        request.get_by_role('button', name=f'Accept {ALICE[0]}').click()

        carol.locator('#usersList button', has_text=ALICE[0]).wait_for()
        alice.locator('#usersList button', has_text=CAROL[0]).wait_for()
        assert carol.locator('#usersList button', has_text=BOB[0]).count() == 0
        assert carol.locator('#contactRequests').is_hidden()
    finally:
        alice.context.close()
        carol.context.close()


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('width', WIDTHS, ids=list(WIDTHS))
def test_chat_menu_and_contact_info(browser, server, width, theme):
    """The menu beside a contact's name, open; then Contact info."""
    page = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, ALICE)
    try:
        if width == 'phone':
            page.click('#sidebarToggle')
        page.locator('#usersList button', has_text=BOB[0]).click()
        page.click('#chatMenuBtn')
        page.wait_for_selector('#chatMenu:not([hidden])')
        check(page, f'chat-menu-{width}-{theme}')
        page.locator('#chatMenu [role="menuitem"]', has_text='Contact info').click()
        page.wait_for_selector('#contactInfoModal:not(.hidden)')
        check(page, f'contact-info-{width}-{theme}')
    finally:
        page.context.close()


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('width', WIDTHS, ids=list(WIDTHS))
def test_encryption_keys(browser, server, width, theme):
    """The key manager, with a contact's safety number and its "Mark as
    verified" control: the one dialog the scan had never opened."""
    alice = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, ALICE)
    bob = sign_in(themed(new_page(browser, *WIDTHS[width]), theme), server, BOB)
    try:
        if width == 'phone':
            alice.click('#sidebarToggle')
        alice.locator('button[title="Encryption keys"]').click()
        alice.wait_for_selector('#keyManagerModal:not(.hidden) .key-verified-toggle')
        check(alice, f'keys-{width}-{theme}')
    finally:
        alice.context.close()
        bob.context.close()


@pytest.mark.parametrize('size', [(1440, 900), (320, 640)], ids=['desktop', 'narrow'])
def test_the_wordmark_is_whole_or_not_shown(browser, server, size):
    """A narrow top bar truncated the wordmark to "Reveal..." (and on a phone
    with large system text, at widths where it would otherwise fit). It is now
    shown whole or, when it cannot fit, visually hidden beside the logo mark,
    still read out as the page's heading."""
    page = sign_in(new_page(browser, *size), server, ALICE)
    try:
        page.wait_for_timeout(300)
        state = page.evaluate('''() => {
            const h1 = document.querySelector('.logo h1');
            const r = h1.getBoundingClientRect();
            return {cut: h1.scrollWidth > h1.clientWidth + 1, width: r.width, text: h1.textContent};
        }''')
        assert state['text'] == 'Reveal-X'
        visible = state['width'] > 1
        assert not (visible and state['cut']), state
        if size[0] == 1440:
            assert visible, state
    finally:
        page.context.close()


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
