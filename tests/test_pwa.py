"""Installability as a Progressive Web App.

Every assertion here corresponds to something that fails silently. A renamed
asset still in the precache list, an icon whose declared size stops matching the
file, a missing iOS meta tag -- none of them raise, none of them show up in the
browser console, and none of them are visible until someone tries to install the
app on a phone and gets a screenshot of the page as their icon.

The service worker itself cannot be exercised from pytest; its request-scoping
logic is covered in tests/js/service-worker.test.mjs. What is checked here is
everything the worker and the manifest *refer to*.
"""

import json
import re
import struct

import pytest

from app import app as flask_app

SW_PATH = 'app/static/sw.js'


@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        yield c


def read_static(name):
    with open(f'app/static/{name}', 'rb') as handle:
        return handle.read()


def png_size(raw):
    """Width and height straight out of the PNG IHDR, without pulling in PIL."""
    assert raw[:8] == b'\x89PNG\r\n\x1a\n', 'not a PNG'
    width, height = struct.unpack('>II', raw[16:24])
    return width, height


@pytest.fixture
def manifest():
    return json.loads(read_static('manifest.json'))


@pytest.fixture
def service_worker():
    with open(SW_PATH, encoding='utf-8') as handle:
        return handle.read()


# ----------------------------------------------------------------------
# The manifest
# ----------------------------------------------------------------------

def test_manifest_is_served_as_json(client):
    response = client.get('/static/manifest.json')
    assert response.status_code == 200
    assert 'json' in response.headers['Content-Type']


@pytest.mark.parametrize('field', ['name', 'short_name', 'start_url', 'display', 'icons'])
def test_manifest_has_the_fields_chrome_requires(manifest, field):
    """Miss any of these and the install prompt is never offered."""
    assert manifest.get(field), f'manifest is missing {field}'


def test_display_mode_launches_without_browser_chrome(manifest):
    assert manifest['display'] in ('standalone', 'fullscreen', 'minimal-ui')


def test_manifest_declares_both_icon_sizes_chrome_wants(manifest):
    sizes = {icon['sizes'] for icon in manifest['icons']}
    assert '192x192' in sizes, 'no 192px icon'
    assert '512x512' in sizes, 'no 512px icon'


def test_a_maskable_icon_exists(manifest):
    """Without one, Android draws the icon into a white circle with a border."""
    assert any('maskable' in (icon.get('purpose') or '') for icon in manifest['icons'])


def test_icon_files_are_the_size_they_claim(client, manifest):
    """`sizes` is what the browser picks on; a wrong value picks a wrong icon.

    This project previously declared one 262x142 file as both 192x192 and
    512x512, which left it uninstallable.
    """
    for icon in manifest['icons']:
        response = client.get(icon['src'])
        assert response.status_code == 200, f"{icon['src']} is not served"
        declared = tuple(int(n) for n in icon['sizes'].split('x'))
        assert png_size(response.data) == declared, \
            f"{icon['src']} declares {icon['sizes']} but is not that size"


def test_splash_and_status_bar_match_the_app(manifest):
    """A background_color away from the page base flashes on the splash-to-app
    transition, and theme_color paints the status bar a colour the UI contains
    or does not."""
    assert manifest['background_color'] == '#070b18'
    assert manifest['theme_color'] == '#070b18'


# ----------------------------------------------------------------------
# iOS, which ignores the manifest completely
# ----------------------------------------------------------------------

@pytest.mark.parametrize('page', ['/', '/lab'])
@pytest.mark.parametrize('needle, why', [
    ('name="apple-mobile-web-app-capable" content="yes"',
     'without it iOS launches the installed app inside Safari chrome'),
    ('rel="apple-touch-icon"',
     'without it iOS uses a screenshot of the page as the home-screen icon'),
    ('name="apple-mobile-web-app-title"',
     'without it the home-screen label is the full <title>'),
    ('name="theme-color"',
     'used before the manifest is fetched'),
    ('rel="manifest"',
     'no manifest, no install prompt'),
])
def test_ios_and_theme_tags_are_present(client, page, needle, why):
    html = client.get(page).get_data(as_text=True)
    assert needle in html, f'{page} is missing {needle!r}: {why}'


def test_apple_touch_icon_resolves_and_is_opaque(client):
    """iOS composites transparency unpredictably and masks the corners itself."""
    html = client.get('/').get_data(as_text=True)
    href = re.search(r'rel="apple-touch-icon" href="([^"]+)"', html).group(1)
    response = client.get(href)
    assert response.status_code == 200, f'{href} is not served'
    assert png_size(response.data) == (180, 180)
    # Colour type 2 is truecolour without alpha; 6 would carry an alpha channel.
    assert response.data[25] == 2, 'apple-touch-icon should not have transparency'


# ----------------------------------------------------------------------
# The precache list
# ----------------------------------------------------------------------

def precached_paths(source):
    block = re.search(r'const ASSETS_TO_CACHE = \[(.*?)\];', source, re.S)
    assert block, 'ASSETS_TO_CACHE not found in sw.js'
    return re.findall(r"'([^']+)'", block.group(1))


def test_every_precached_asset_exists(client, service_worker):
    """cache.addAll() is atomic: one 404 rejects the whole install, silently,
    and the app has no offline cache at all with nothing else looking wrong."""
    missing = [p for p in precached_paths(service_worker) if client.get(p).status_code != 200]
    assert missing == [], f'precached but not served: {missing}'


def test_no_html_is_precached(service_worker):
    """A cached app shell is the classic "needs a hard refresh" bug: the page
    comes back listing the old scripts, so nothing new is ever requested."""
    for path in precached_paths(service_worker):
        assert re.search(r'\.(css|js|png|json|onnx|woff2?)$', path), \
            f'{path} is precached but is not a static asset'


def test_the_service_worker_is_served_from_the_root(client):
    """Registered at /static/sw.js its scope is /static/, so it would control
    none of the pages anyone visits."""
    response = client.get('/sw.js')
    assert response.status_code == 200
    assert response.headers.get('Service-Worker-Allowed') == '/'
    assert 'javascript' in response.headers['Content-Type']


def test_the_cache_name_is_versioned(service_worker):
    """Shipping a worker change without bumping this leaves users on the old one."""
    name = re.search(r"const CACHE_NAME = '([^']+)'", service_worker)
    assert name, 'CACHE_NAME not found'
    assert re.match(r'^revealx-cache-v\d+', name.group(1))


# ----------------------------------------------------------------------
# Install and update handling
#
# These cover the two things a user can act on. Both fail silently when
# broken: a missing manifest field costs the install prompt, and an update
# that activates without asking reloads the page under whoever is typing.
# ----------------------------------------------------------------------

def test_manifest_declares_a_scope_and_id(manifest):
    """Without a scope, navigations outside it drop back to the browser.

    `id` keeps the installed app pointing at the same entry after a change to
    start_url; without it the browser treats the new URL as a different app.
    """
    assert manifest['scope'] == '/'
    assert manifest['id'] == '/'


def test_shortcuts_point_at_routes_that_exist(client, manifest):
    shortcuts = manifest.get('shortcuts', [])
    assert shortcuts, 'no home-screen shortcuts declared'
    for shortcut in shortcuts:
        assert shortcut['name'] and shortcut['url']
        assert client.get(shortcut['url']).status_code == 200, shortcut['url']


def test_the_worker_does_not_activate_itself_on_install(service_worker):
    """skipWaiting() inside install reloads the page under the user.

    In a chat application that costs an unsent message, so activation is
    gated behind the user accepting the update banner instead.
    """
    install_block = service_worker.split("addEventListener('install'")[1]
    # The message handler follows the install handler and is allowed to call
    # skipWaiting; cut the block before it so only install itself is checked.
    install_block = re.split(r"addEventListener\('(?:message|activate)'", install_block)[0]
    code = '\n'.join(
        line for line in install_block.splitlines()
        if not line.lstrip().startswith('//')
    )
    assert 'skipWaiting' not in code


def test_the_worker_activates_early_only_when_asked(service_worker):
    assert "addEventListener('message'" in service_worker
    assert 'SKIP_WAITING' in service_worker
    assert 'self.skipWaiting()' in service_worker


def test_pwa_script_is_served_and_precached(client, service_worker):
    assert '/static/js/pwa.js' in service_worker
    response = client.get('/static/js/pwa.js')
    assert response.status_code == 200
    assert b'beforeinstallprompt' in response.data


@pytest.mark.parametrize('element_id', [
    'installAppBtn',        # Android/Chrome install entry point
    'iosInstallHint',       # iOS has no install API, so it needs instructions
    'updateBanner',
    'updateAcceptBtn',
    'updateDismissBtn',
])
def test_the_page_carries_the_controls_pwa_js_binds_to(client, element_id):
    """pwa.js binds by id and no-ops when an element is absent, so a renamed
    or deleted element removes the feature without any error."""
    page = client.get('/').get_data(as_text=True)
    assert f'id="{element_id}"' in page


def test_ios_install_route_is_spelled_out(client):
    """iOS never fires beforeinstallprompt. If the page does not say
    'Add to Home Screen', iPhone users cannot discover that it installs."""
    page = client.get('/').get_data(as_text=True)
    assert 'Add to Home Screen' in page


def test_layout_uses_dynamic_viewport_height():
    """100vh on iOS Safari is the toolbar-hidden height, so the composer ends
    up under the bottom bar with no way to scroll to it."""
    with open('app/static/css/style.css', encoding='utf-8') as handle:
        css = handle.read()
    assert '100dvh' in css
    assert 'env(safe-area-inset-bottom' in css
