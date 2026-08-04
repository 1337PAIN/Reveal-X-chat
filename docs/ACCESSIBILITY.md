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
| Chat + settings, dark and light | 53 | 0 | 0 |
| Sign-in screen, dark and light | 5 | 0 | 0 |
| `/lab` | 56 | 0 | 0 |

Tightest margins, which are the ones to watch when changing colours:

| Element | Ratio | Needs |
| --- | --- | --- |
| `.logo h1` gradient wordmark (light) | 5.09 | 4.5 (large) |
| `.message-time` on a sent bubble (light) | 7.47 | 7 |
| `.message-username` (dark) | 7.52 | 7 |
| `.primary-btn` on `/lab`, hover state | 7.72 | 7 |

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

### Known limits

- Elements using `backdrop-filter` are flagged `blur: true`. Their true backdrop
  is whatever pixels are behind them, which cannot be read from the CSSOM. The
  composited layer colours are the standard approximation and are what automated
  tooling uses, but a strong image behind a blurred panel could differ.
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
