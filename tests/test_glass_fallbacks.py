"""Every blurred surface has to have a way out of being blurred.

`backdrop-filter` is the whole glassmorphism design, and it is also the one
property in it that some people need switched off. Two different conditions ask
for that, and they are not the same question:

  @supports not (backdrop-filter)      the browser cannot blur
  prefers-reduced-transparency: reduce the user asked us not to

Neither of them fails loudly. A surface that answers neither still renders --
it just renders as translucent text over a drifting aurora, for the one person
who explicitly set a system preference to avoid exactly that. Nothing throws,
and the person who needs it is the least likely to be the one demonstrating the
project.

Motion is the third condition, and it behaves the same way:

  prefers-reduced-motion: reduce       the user asked us not to animate

So these are rule tests, not appearance tests. They do not care what the glass
looks like. They assert that for every selector in the app that turns blur ON,
some reduced-transparency block turns it back OFF, and that every looping
animation has a reduced-motion answer -- discovering both sets by parsing, so
a new blurred panel or a new loop is caught without anybody remembering to
list it here.

Found by these rules when they were first written: `.pwa-banner` answered
neither transparency condition, `.lock-screen`/`.lock-card` answered only the
first -- on the passcode screen, which is the one screen a user cannot
navigate away from -- and `.spinner` rotated forever with no motion answer.
"""

import re

import pytest

STYLESHEETS = [
    'app/static/css/style.css',
    'app/static/css/glass.css',
    'app/static/css/settings.css',
    'app/static/css/rx_lab.css',
]

REDUCED_TRANSPARENCY = '@media (prefers-reduced-transparency: reduce)'

# style.css owns tokens, layout and colour; glass.css's own header states that
# it is the file that "turns those translucent surfaces into actual glass".
# A blur declared anywhere else escapes the single place that disables them,
# which is how .pwa-banner came to have no fallback at all.
BLUR_IS_NOT_DECLARED_IN = 'app/static/css/style.css'


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def rules(css):
    """(selector, body) for every rule, including rules nested in @media.

    Brace-matched rather than regex-matched: a media block's body contains
    rules with braces of their own, and a regex that stops at the first `}`
    reads a media prelude as a selector.
    """
    found = []
    for index, char in enumerate(css):
        if char != '{':
            continue
        selector = css[:index].rsplit('}', 1)[-1].rsplit('{', 1)[-1]
        selector = re.sub(r'/\*.*?\*/', '', selector, flags=re.S).strip()
        depth, end = 1, index + 1
        while depth and end < len(css):
            if css[end] == '{':
                depth += 1
            elif css[end] == '}':
                depth -= 1
            end += 1
        found.append((selector, css[index + 1:end - 1]))
    return found


def selectors_in(selector_list):
    """The individual selectors of a comma-separated list, each reduced to the
    part before any pseudo-class, so `.btn:hover` counts as `.btn`."""
    for one in selector_list.split(','):
        one = one.strip()
        if one and not one.startswith('@'):
            yield one.split(':')[0].strip()


def media_blocks(css, prelude):
    """Every block body declared under a given @media prelude."""
    return [body for selector, body in rules(css)
            if selector.replace(' ', '') == prelude.replace(' ', '')]


def blur_on(css):
    """Selectors that switch backdrop-filter on."""
    return {name
            for selector, body in rules(css)
            if not selector.startswith('@')
            and re.search(r'backdrop-filter\s*:\s*(?!none)', body)
            for name in selectors_in(selector)}


def blur_off_under(css, prelude):
    """Selectors that a given @media block switches backdrop-filter off for."""
    off = set()
    for block in media_blocks(css, prelude):
        for selector, body in rules(block):
            if re.search(r'backdrop-filter\s*:\s*none', body):
                off.update(selectors_in(selector))
    return off


@pytest.mark.parametrize('path', STYLESHEETS)
def test_every_blurred_surface_honours_reduced_transparency(path):
    css = read(path)
    blurred = blur_on(css)
    if not blurred:
        pytest.skip(f'{path} declares no blur')

    exempt = blur_off_under(css, REDUCED_TRANSPARENCY)
    missing = sorted(blurred - exempt)

    assert not missing, (
        f'{path}: {len(missing)} blurred surface(s) ignore '
        f'prefers-reduced-transparency: {", ".join(missing)}. Add them to the '
        f'{REDUCED_TRANSPARENCY} block in the same file, with '
        f'backdrop-filter: none and an opaque background -- a surface that '
        f'only drops the blur is still translucent over the aurora.'
    )


@pytest.mark.parametrize('path', STYLESHEETS)
def test_reduced_transparency_block_is_not_stale(path):
    """A selector listed in the fallback that nothing blurs any more is dead
    weight, and -- worse -- reads as coverage that is not there."""
    css = read(path)
    exempt = blur_off_under(css, REDUCED_TRANSPARENCY)
    if not exempt:
        pytest.skip(f'{path} has no reduced-transparency block')

    stale = sorted(exempt - blur_on(css))
    assert not stale, (
        f'{path}: the reduced-transparency block names {", ".join(stale)}, '
        f'which nothing blurs. Either the blur moved and its new selector is '
        f'now uncovered, or this line can go.'
    )


def test_blur_is_declared_only_in_the_glass_layer():
    """style.css is tokens, layout and colour. Blur declared there bypasses
    the one block that turns blur off, which is exactly what happened to the
    install banner."""
    blurred = blur_on(read(BLUR_IS_NOT_DECLARED_IN))
    assert not blurred, (
        f'{BLUR_IS_NOT_DECLARED_IN} declares backdrop-filter on '
        f'{", ".join(sorted(blurred))}. Move it to glass.css and add the '
        f'selector to that file\'s fallback blocks.'
    )


def test_the_passcode_screen_can_be_read_without_blur():
    """The one screen with no way around it. It had the @supports fallback and
    not the preference one, so a user who had asked for reduced transparency
    got a blurred unlock prompt and no means of dismissing it."""
    css = read('app/static/css/settings.css')
    assert {'.lock-screen', '.lock-card'} <= blur_off_under(css, REDUCED_TRANSPARENCY)

    # Dropping the blur is only half of it: the card still has to stop being
    # see-through, or the aurora reads straight through the passcode field.
    # Checked by parsing rather than by pattern. This began as a regex for
    # `.lock-card { ... background: # ... }` anywhere in the block, which the
    # light-theme rule `body[data-theme="light"] .lock-card` satisfied on its
    # own -- so it passed with the dark card left translucent.
    for theme, wanted in (('dark', '.lock-card'),
                          ('light', 'body[data-theme="light"] .lock-card')):
        bodies = [body
                  for block in media_blocks(css, REDUCED_TRANSPARENCY)
                  for selector, body in rules(block)
                  if wanted in list(selectors_in(selector))]
        assert bodies, f'no {theme} .lock-card rule under reduced transparency'
        assert any(re.search(r'background:\s*#', body) for body in bodies), (
            f'the {theme} lock card drops its blur but keeps a translucent '
            f'background, so the aurora still reads through the passcode field'
        )


# --------------------------------------------------------------------------
# Motion
#
# Coverage is per page, not per file: the aurora's animation is declared in
# style.css and switched off in glass.css, so a file-at-a-time check reports
# a gap that does not exist. These are the stylesheets each page actually
# loads, in load order.
# --------------------------------------------------------------------------

BUNDLES = {
    'chat': ['app/static/css/style.css',
             'app/static/css/glass.css',
             'app/static/css/settings.css'],
    'lab': ['app/static/css/rx_lab.css'],
}

REDUCED_MOTION = '@media (prefers-reduced-motion: reduce)'


def without(css, prelude):
    """`css` with every block under `prelude` cut out.

    Needed because a reduced-motion block may declare a *replacement*
    animation, and a scan that cannot tell inside from outside reads that
    replacement as a fresh offender.
    """
    target = prelude.replace(' ', '')
    kept, index = [], 0
    while index < len(css):
        brace = css.find('{', index)
        if brace == -1:
            kept.append(css[index:])
            break
        selector = css[index:brace].rsplit('}', 1)[-1].strip()
        if selector.replace(' ', '') != target:
            kept.append(css[index:brace + 1])
            index = brace + 1
            continue
        depth, end = 1, brace + 1
        while depth and end < len(css):
            if css[end] == '{':
                depth += 1
            elif css[end] == '}':
                depth -= 1
            end += 1
        kept.append(css[index:brace - len(selector)])
        index = end
    return ''.join(kept)


def looping(css):
    """Selectors running an animation that never ends."""
    found = {}
    for selector, body in rules(css):
        if selector.startswith('@'):
            continue
        match = re.search(r'(?<!-)animation\s*:\s*([^;]+)', body)
        if match and 'infinite' in match.group(1):
            for name in selectors_in(selector):
                found[name] = match.group(1).strip()
    return found


def motion_answers(css):
    """Selectors a reduced-motion block gives an animation answer for --
    either switching it off or swapping it for something non-vestibular."""
    answered = set()
    for block in media_blocks(css, REDUCED_MOTION):
        for selector, body in rules(block):
            if re.search(r'(?<!-)animation\s*:', body):
                answered.update(selectors_in(selector))
    return answered


@pytest.mark.parametrize('page', sorted(BUNDLES))
def test_every_looping_animation_answers_reduced_motion(page):
    bundle = '\n'.join(read(path) for path in BUNDLES[page])
    loops = looping(without(bundle, REDUCED_MOTION))
    missing = sorted(set(loops) - motion_answers(bundle))

    assert not missing, (
        f'{page}: animation that never stops and ignores '
        f'prefers-reduced-motion: ' +
        ', '.join(f'{name} ({loops[name]})' for name in missing)
    )


def test_the_spinner_still_says_it_is_working():
    """Not every loop should simply be switched off. This one runs on the
    Reconstruct button -- the slowest operation in the app -- and is the only
    sign the work started, so a stopped spinner reads as a hung one. It keeps
    an animation; what it must lose is the rotation."""
    css = read('app/static/css/style.css')
    block = '\n'.join(media_blocks(css, REDUCED_MOTION))

    spinner = [body for selector, body in rules(block)
               if '.spinner' in list(selectors_in(selector))]
    assert spinner, '.spinner has no reduced-motion rule'

    declared = re.search(r'(?<!-)animation\s*:\s*([^;]+)', spinner[0])
    assert declared, '.spinner declares no reduced-motion animation'
    assert 'none' not in declared.group(1), (
        'the spinner was switched off rather than replaced, which leaves the '
        'Reconstruct button with no sign that anything is happening'
    )

    # Whatever replaced it must not rotate, or the substitution bought nothing.
    replacement = declared.group(1).split()[0]
    frames = re.search(
        r'@keyframes\s+' + re.escape(replacement) + r'\s*\{(.*?)\n\}',
        css, re.S)
    assert frames, f'@keyframes {replacement} is not defined'
    assert not re.search(r'transform\s*:\s*rotate', frames.group(1)), (
        f'{replacement} rotates, so reduced motion still gets a spinning '
        f'element -- the thing the preference exists to avoid'
    )
