# Accessibility — contrast (milestone M6)

Reveal-X meets **WCAG 2.1 Level AAA for contrast** (success criterion 1.4.6) on
every text element in the chat, the settings panel, the sign-in screen and
`/lab`, in **both** the dark and light themes.

That is a measured claim, not a design intention. It can be re-checked in about
ten seconds — see [Re-running the audit](#re-running-the-audit).

## The bar

| Text | AA (1.4.3) | AAA (1.4.6) |
| --- | --- | --- |
| Normal (< 18.66px, or < 24px when not bold) | 4.5:1 | **7:1** |
| Large (≥ 24px, or ≥ 18.66px bold) | 3:1 | **4.5:1** |

## Result

| Surface | Elements checked | Below AA | Below AAA |
| --- | --- | --- | --- |
| Chat + settings, dark and light | 40 | 0 | 0 |
| Sign-in screen, dark and light | 14 | 0 | 0 |
| `/lab` | 56 | 0 | 0 |

Tightest margins, which are the ones to watch when changing colours:

| Element | Ratio | Needs |
| --- | --- | --- |
| `#userStatus` "Online" (light) | 7.33 | 7 |
| `#userCount` (light) | 7.63 | 7 |
| `.summary-card` labels on `/lab` | 7.85 | 7 |
| `.logo h1` gradient wordmark (dark) | 6.38 | 4.5 (large) |

## Correction: these numbers were wrong once

An earlier revision of this document claimed AAA on the strength of an audit
that could not see the page's own background.

The design paints its aurora into a **fixed `body::before` at `z-index: -1`**.
The audit script walked up the DOM to `body`, found `background-color:
#070b18`, and composited every translucent panel onto *that* — a near-black
that is nowhere on screen. The pixels actually behind the header are a bright
blue around `rgb(65,119,191)`. Light ink measured against near-black looks
excellent and against bright blue does not, so every glass surface in the app
was over-reported, in some places by a factor of three.

What that hid, measured against the real painted backdrop:

| Element | Reported | Actually | Bar |
| --- | --- | --- | --- |
| `.logo h1` wordmark, purple stop (dark) | 5.27 | **1.94** | 3 (AA, large) |
| `.welcome-icon` "RX" | 1.46 | **1.42** | 3 (AA, large) |
| `#logoutBtn` / `AI Lab` in the header | 11.05 | **3.87** | 4.5 (AA) |
| `#settingsBtn` | — | **3.63** | 4.5 (AA) |
| `#userStatus` "Online" | — | **3.54** | 4.5 (AA) |
| 21 elements on `/lab` (worst `.summary-card` label) | — | **5.62** | 7 (AAA) |

Five AA failures and thirty-seven AAA failures, in a document that said zero.
The wordmark one was visible to the naked eye — it is what prompted the
re-check — and the audit had been signing it off the whole time.

The script now rasterises decorative `html`/`body` pseudo-element backgrounds
into a viewport-sized canvas and samples each element's own position, including
the aurora's current animation transform. Rows report `backdropSource:
"painted"` when they were measured this way and `"cssom"` when they fall back.
All 110 rows above are `painted`.

**The lesson worth keeping:** a passing audit is only as good as its model of
the page. This one was precise, well-commented, and confidently wrong, and it
stayed wrong because its output agreed with what everyone wanted to be true.

## What had to change

Before the audit the light theme had **8 elements below AA** — meaning the
"WCAG 2.1 AA" comment that was already in `style.css` was not accurate. Both
themes had failures once gradients were measured properly.

| Problem | Was | Now |
| --- | --- | --- |
| Selected account row: white text on a pale teal gradient | **1.30:1** light, 2.90:1 dark | 8.31:1 / 8.38:1 (opaque `#1f4e8c → #14574f`) |
| Primary/send buttons: white on a translucent indigo | 4.22:1 light, 6.30:1 dark | 8.72:1 (opaque `#3730a3 → #1e40af`) |
| Danger button | 4.21:1 | 8.02:1 (`#991b1b → #9f1239`) |
| `--text-secondary` on a sent bubble | 4.08:1 light, 5.08:1 dark | 7.48:1 / 7.58:1 |
| `--success` ("Read" receipt) on a sent bubble | 3.62:1 | 7.94:1 |
| `--accent` (message author name) | 3.95:1 | 7.73:1 |
| `/lab` run button | 6.50:1, and **5.96:1 on hover** | 9.16:1, 7.72:1 on hover |
| Wordmark gradient text | 3.54:1 dark | 5.25:1 dark, 5.09:1 light |

Then, in the round that produced the correction above:

| Problem | Was | Now |
| --- | --- | --- |
| Dark-theme glass tinted **white** over a bright aurora | `rgba(255,255,255,.05/.08/.13)` | `rgba(11,17,38,.55/.68/.78)` |
| `/lab` panes, same mistake | `rgba(255,255,255,.06/.10/.14)` | `rgba(11,17,38,.55/.65/.74)` |
| Wordmark purple stop (dark) | `#b07fc6`, 1.94:1 | `#c79ad9`, 6.38:1 |
| Wordmark purple stop (light) | `#7f4396`, 4.54:1 | `#63307c`, 6.41:1 |
| `.welcome-icon` ink left teal after glass.css made the tile teal | `var(--accent)`, 1.42:1 | `#0b1226`, 6.71:1 |
| `--text-secondary` (light) on the aurora-lit sidebar | `#333c53`, 6.82:1 | `#2d3549`, 7.63:1 |

The first two rows are one idea: **dark themes need dark glass.** The light
theme already knew this — its note below explains that white glass on a
near-white base flattened the UI — but the dark theme kept white fills, and
white glass over a *bright* aurora composites upwards until light ink has
nothing left to stand on. Tinting with the page's own base colour keeps roughly
45% of the aurora showing through, so the panes still refract.

Two structural notes:

- **Translucent fills were the root cause.** A fill like
  `rgba(96,165,250,.92)` has no fixed contrast — it depends on what is behind
  it, so the same button measured 4.2:1 on the light theme and 6.3:1 on the
  dark one. The semantic buttons and the selected row now use **opaque**
  fills, which are theme-independent and can be verified once.
- **`settings.css` loads after `glass.css`.** A rule there was re-softening
  the selected row's status text to `rgba(255,255,255,.85)` and winning at
  equal specificity, quietly undoing a value that had been measured. It is now
  opaque in both files.

## Re-running the audit

`app/static/js/a11y-audit.js` is a development tool. **No page loads it** — it
ships nothing to users. Open any page, then in the browser console:

```js
const s = document.createElement('script');
s.src = '/static/js/a11y-audit.js';
document.head.appendChild(s);
```

Then either inspect one state:

```js
console.table(revealxContrastAudit().filter(r => !r.passAAA));
```

or sweep both themes with the settings panel opened:

```js
revealxContrastSweep().then(r => console.log(r.checked, r.failAA.length, r.failAAA.length));
```

### Why it is not a two-line script

Three things a naive contrast checker gets wrong, each of which produced a false
result while this audit was being done:

1. **Gradients.** `getComputedStyle().backgroundColor` is `transparent` on an
   element filled by `linear-gradient()`, so a naive check reads through to the
   page behind it. That made solid indigo buttons look like white-on-white
   (1.05:1) and briefly suggested five failures that did not exist. Every
   gradient stop is evaluated and the worst is reported.
2. **Gradient text.** The wordmark uses `background-clip: text` with a
   transparent `-webkit-text-fill-color`. There the gradient *is* the ink and
   `color` is never painted — treating it as a background inverts the
   measurement entirely.
3. **Occlusion.** Text behind an open modal is not visible text. Without a hit
   test, auditing the sign-in screen reports the whole app chrome sitting behind
   the login dialog — twelve "failures" nobody can see.
4. **Decorative page backdrops.** See the correction above: stopping the walk at
   `body`'s `background-color` ignores a full-viewport `body::before`, and every
   glass surface in the app is sitting on one.
5. **Transitions mid-flight.** Switching theme animates `background` on every
   control that has a transition, and `getComputedStyle` during the animation
   returns the interpolated value. Run in a background tab, where frames are
   throttled and the interpolation never advances, the sweep reads the previous
   theme's surfaces under the new theme's ink — ten confident AA failures that
   did not exist. `revealxContrastSweep` now freezes transitions while it
   measures.

### Known limits

- `backdrop-filter: blur()` is approximated as identity. `saturate()` and
  `brightness()` are applied properly, but a blur is only a no-op when the
  backdrop is locally smooth. That holds for this aurora — a broad gradient
  field — and would not hold for a photograph or a busy pattern. Rows carrying
  a backdrop-filter are still flagged `blur: true`.
- The backdrop rasteriser understands the gradient forms this codebase uses:
  `radial-gradient` with explicit pixel radii, and `linear-gradient` with an
  angle. Anything else marks the run `painted-partial` rather than guessing.
- Only **contrast** (1.4.3 / 1.4.6) is measured. WCAG AAA as a whole includes
  criteria this project does not claim — sign-language alternatives for video
  (1.2.6), extended audio description (1.2.7), reading level (3.1.5) and
  context-sensitive help (3.3.5) among them. The claim here is scoped to
  contrast, deliberately and explicitly.
- The audit covers the states reachable in a sweep: both themes, chat, settings,
  sign-in and `/lab`. Transient states (toasts, the TOTP modal, the
  reconstruction panel mid-fetch) are not visited automatically.

## Other accessibility work

Not measured by the script above, but present:

- `aria-live` region (`#srAnnouncements`) announcing tamper verdicts, network
  changes and panel transitions to screen readers.
- `:focus-visible` indicators (`style.css`).
- `prefers-reduced-motion`, `prefers-reduced-transparency` and
  `@supports not (backdrop-filter)` fallbacks, so the glass layer degrades to
  solid surfaces rather than becoming unreadable.
- Responsive down to 375px with 44px minimum touch targets.
