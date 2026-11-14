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
| Chat + settings, dark and light, with a text message and an image share on screen (re-measured 14 Nov 2026) | 55 | 0 | 0 |
| Sign-in screen, dark and light | 14 | 0 | 0 |
| `/lab` | 56 | 0 | 0 |

Tightest margins, which are the ones to watch when changing colours:

| Element | Ratio | Needs |
| --- | --- | --- |
| `#userStatus` "Online" (light) | 7.33 | 7 |
| `#userCount` (light) | 7.63 | 7 |
| `.summary-card` labels on `/lab` | 7.85 | 7 |
| `.logo h1` gradient wordmark (dark) | 6.38 | 4.5 (large) |
| `.message-menu-item.danger` (dark) | 7.29 | 7 |
| `.message-menu-item.danger` (light) | 7.69 | 7 |
| Sent bubble time and status (light) | 7.82 | 7 |

The 40-element run above missed message bubbles altogether: re-measured with
a share and a sent message on screen, the "ONE-TIME ACCESS SHARE" notice was
3.34:1 (below AA) and the light theme's sent-bubble name, time and status were
6.34-6.87:1. The notice is now a neutral chip and those three are coloured for
that bubble (`tests/test_message_meta_contrast.py`).

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

## Automated check: axe-core

axe-core 4.10.2 was run in the browser against the sign-in screen, the chat
with a conversation open, the settings panel and `/lab`, in both themes
(14 Nov 2026), with its AAA contrast rule (`color-contrast-enhanced`)
switched on as well as the default WCAG A/AA rules: **0 violations** on
every screen. `/lab` has one theme only, so it was checked once. axe is not a
project dependency and nothing loads it: it was injected for the run from a
copy outside the repository.

Two things to know when repeating it:

- **Freeze transitions first.** Run after toggling the theme, axe reported
  eight contrast failures (2.2:1) that were the previous theme's surfaces
  under the new theme's text, mid-animation -- the same trap as point 5
  above. In a background tab the animation never finishes, so waiting is not
  enough; add `*, *::before, *::after { transition: none !important;
  animation: none !important; }` before running. With that, there were
  none.
- **Its name computation caught what its rules did not.** No rule failed on
  the contact list, but `axe.commons.text.accessibleText()` gave a contact's
  name as "e2ebobOffline". That is now fixed (below).

## Keyboard-only walkthrough

Done with real key presses, no pointer, in the in-app browser on
14 Nov 2026, signed in as a test account. Each step says where focus went.

| Step | Keys | Result |
| --- | --- | --- |
| Sign in | page loads | Focus starts in the username box (remembered username pre-filled) |
| | Shift+Tab | Wraps to "Request account", the dialog's last control -- not into the chat behind |
| | Tab, type password, Enter | Signed in; focus on the account search |
| Open a chat | Tab | The contact, read as "e2ebob, Offline" |
| | Enter | Chat opens; focus moves to the message box |
| Send | type, Enter | Sent, end-to-end encrypted; focus stays in the message box |
| React | Shift+Tab ×4 | "Add a reaction" on the new message |
| | Enter | Picker opens; focus on "React with 👍" |
| | Right, Enter | Reacted with ❤️; picker closes; focus back on "Add a reaction" |
| | Enter, Escape | Picker opens and closes; focus back on the button |
| Delete | Tab, Enter | "More actions" menu opens; focus on "Delete for everyone" |
| | Enter | Confirmation opens; focus on **Cancel** |
| | Tab / Shift+Tab | Cycles Cancel and "Delete for everyone" only |
| | Shift+Tab, Enter | Deleted from both sides; focus on the message box |
| Settings | Shift+Tab to Settings, Enter | Settings opens; focus on "Close settings" |
| | Shift+Tab | Wraps to "Save Changes", the panel's last control |
| Close | Escape | Settings closes; focus back on the Settings button |

Every step that failed on the first pass is fixed and listed under "Other
accessibility work": the dialog let Shift+Tab out, the contact name ran
together, the reaction picker never took focus, settings neither took focus
nor gave it back, and focus fell to `<body>` after signing in and after a
delete.

## Other accessibility work

Not measured by the script above, but present:

- `aria-live` region (`#srAnnouncements`) announcing tamper verdicts, network
  changes and panel transitions to screen readers.
- `:focus-visible` indicators (`style.css`).
- `prefers-reduced-motion`, `prefers-reduced-transparency` and
  `@supports not (backdrop-filter)` fallbacks, so the glass layer degrades to
  solid surfaces rather than becoming unreadable.
  On a phone every dialog is a bottom sheet; it used to rest off the bottom
  of the screen (`translateY(100%)`) and rely on its slide-up animation to
  arrive, so it was invisible whenever the animation did not play, and the
  slide ignored reduced motion. It now rests in place, slides only as an
  entrance, and does not slide at all under reduced motion
  (`tests/test_bottom_sheet_motion.py`). The same goes for each new message
  bubble's slide-in, the offline banner's slide-down and the activity flash,
  none of which had a reduced-motion override, so a busy chat was never still
  (`tests/test_reduced_motion_entrances.py`). The spinner keeps its existing
  cross-fade substitute.
- **Controls draw their icons, they do not borrow emoji.** Settings,
  rebuild, theme, camera, microphone, discard, keys, react, "more" and the
  lock screen used emoji, which each platform draws in its own font and
  colour -- a gear on one phone, a cog on another -- and which cannot follow
  the text colour into the light theme. They are now one inline SVG sprite
  stroked in `currentColor`, so an icon has its button's text contrast in
  both themes. Each stays `aria-hidden`, the button's `aria-label` carrying
  the name (`icons.js`, `tests/js/icons.test.mjs`, `tests/test_svg_icons.py`).
  Emoji that are part of a status's wording, such as "🔒 Key exchanged",
  are left as text.
- The offline indicator shows a crossed-out wifi icon, drawn as inline SVG
  and named "Offline". It used to ask Font Awesome for `fa-wifi-slash`,
  which the free set does not have, so it showed an empty box; the library
  (about 400 KB of CSS and fonts, used for nothing else) is gone
  (`tests/test_startup_path.py`).
- Responsive down to 375px with 44px minimum touch targets. A bubble's meta
  row wraps: on a share it carries the "Opens once" chip, the time, the
  status and two 44px buttons, about 320px, and on a phone it used to spill
  out past the bubble's left edge (`tests/test_message_meta_wraps.py`). `/lab` holds at
  320, 375 and 390px with no sideways scroll: its single-column grid used to
  size itself to the metrics table's 325px minimum, which pushed every card
  9px past the screen at 375px (`tests/test_mobile_layout.py`, lab rules).
- **Message state is words, and red means danger.** Each message carries one
  chip -- "E2EE", "Not encrypted", or "Opens once - N min left" -- in
  `--text-primary` with only the border tinted (`chips.js`). It replaces a bare
  header padlock with no text, a red "[not encrypted]" badge, and a red
  "ONE-TIME ACCESS SHARE" notice that measured 3.34:1, failing even AA
  (`tests/js/chips.test.mjs`, `tests/test_message_chips.py`).
- **Send keeps its name during the two-second cooldown.** The label used to
  become a countdown ("2s", "1s") and the button was disabled, so a screen
  reader announced a button called "2s" and keyboard focus fell out of the
  composer. It now stays "Send" with `aria-disabled` and a dimmed state with a
  draining bar (still under reduced motion), and a press during the pause is
  explained (`tests/test_send_cooldown.py`).
- **The composer's limit is the real one, and it says so near the end.** Its
  `maxlength` was 500 while the server allows 2,000, so long messages were cut
  off without a word. A counter appears for the last 200 characters and is
  announced through the polite live region only when a threshold is crossed
  (200, 100, 50, 20, 10 left, the limit), not per keystroke (`composer.js`,
  `tests/js/composer.test.mjs`, `tests/test_composer.py`).
- **No native `alert()`, `confirm()` or `prompt()`.** They block the page,
  cannot be themed, and some screen readers announce them as browser alerts
  unrelated to the app. Questions use the in-app dialog (`askConfirm`),
  must-read messages a one-button notice (`showNotice`), and everything else
  a toast in a live region -- polite for notices, assertive for errors
  (`dialogs.js`; `tests/test_no_native_dialogs.py` scans every shipped
  script).
- **A dropped connection is said in words.** It used to show only as a
  retry count tucked into the header. A banner at the top now reads
  "Reconnecting..." while the socket is down, and "Signed out - sign in
  again" when the server answered `auth_required` and the session could not
  be resumed; it goes once the session is back. It is a polite status region,
  so it does not interrupt what a screen reader is reading. White on
  `#16203a` (16.1:1) and on `#78350f` (9.1:1) in both themes
  (`connection.js`, `tests/js/connection.test.mjs`,
  `tests/test_connection_banner.py`).
- **Icon-only buttons have names.** A button is named from its content
  before its `title`, so "⚙️" was read as "gear" rather than "Settings"; the
  same went for rebuild, theme, keys, camera and microphone. Each now has an
  `aria-label` (the theme button's says what pressing it will do, and the mic's
  changes while recording), the glyphs are `aria-hidden`, and the sidebar
  footer shows a word under each icon (`tests/test_icon_buttons.py`).
- **Every dialog is announced as one, and every close button says what it
  closes.** The settings panel was a plain `<div>` (now `role="dialog"`,
  `aria-modal`, labelled by its heading), and its "×" -- like those of the
  encryption keys and two-factor setup panels -- was read as "times" or
  "Close" without saying which of the stacked panels it closed. The sidebar's
  "Accounts" heading is an `h2` (`tests/test_dialog_semantics.py`).
- **The keyboard stays in the open dialog.** `aria-modal` told a screen
  reader the page behind was out of play, but nothing told the keyboard:
  Shift+Tab from the sign-in dialog's username box went to "Rebuild an image"
  in the chat behind it. Focus that lands outside the topmost open dialog is
  now sent back in -- to its last control on Shift+Tab, its first on Tab --
  so both directions cycle inside it. It is checked as focus moves rather
  than by making the page `inert`: the dialogs open and close in many places,
  and an observer that set `inert` ran after a closing dialog had already
  handed focus back to its trigger, which was still inert, so focus was lost
  (`modal-focus.js`, `tests/js/modal-focus.test.mjs`,
  `tests/test_modal_focus.py`). A dialog that opens without placing focus
  itself (settings did not) is given it, on its first control, and one that
  closes with focus lost -- inside it, or fallen to `<body>` -- hands it back
  to whatever opened it. A dialog that chose its own focus, as the delete
  confirmation does with Cancel, is left alone.
- **Focus is not dropped when what held it goes away.** After signing in,
  focus was in the form that had just been hidden; after deleting a message
  it was on that message's "More actions" button, removed with it. Both
  times it fell to `<body>` and the next Tab started from the top of the
  page. Signing in now puts focus on the account search, and deleting puts it
  on the message box (`tests/test_focus_not_lost.py`).
- **Nothing empty is drawn as if it were content.** The reconstruction
  previews were `<img src="">` -- a broken-image icon whose alt text a screen
  reader announced -- until something loaded; they are now hidden and replaced
  by "Not loaded yet" / "Not reconstructed yet". The sender's share bubble no
  longer draws an empty tamper panel, and /lab's "Enhanced output" card says
  when it is showing the plain reconstruction instead
  (`tests/test_placeholders.py`, `tests/js/lab-format.test.mjs`).
- **Message actions are big enough to hit.** React and "More actions" are at
  least 24 × 24px everywhere (WCAG 2.5.8) and 44 × 44px on touch screens,
  from padding rather than a larger glyph. React used to be about 21 × 17px
  and delete a bare 16px "×" beside the timestamp. Delete now sits in a
  per-message menu (`aria-haspopup`, `aria-expanded`, focus moves in and back,
  Escape closes) and asks in an in-app dialog whose default focus is Cancel,
  instead of `window.confirm()` (`dialogs.js`, `tests/js/dialogs.test.mjs`,
  `tests/test_message_actions.py`). The reaction picker works the same way:
  Enter used to open it but leave focus on the button, with the next Tab
  going to "More actions". Focus now moves to the first reaction, Left and
  Right move between them, and a choice or Escape closes it with focus back
  on the button, which says `aria-expanded`
  (`tests/test_reaction_picker_keyboard.py`). Flipped below a bubble near
  the top of the chat, the picker used to be painted under the next bubble
  (each bubble's `backdrop-filter` makes it its own stacking context) and
  could not be clicked; the bubble holding an open picker or menu is now
  lifted above its neighbours (`tests/test_bubble_popovers_on_top.py`).
- **The contact list works by keyboard.** Each row's clickable part is a
  `<button type="button">` (`contact-row.js`), so Tab reaches it and Enter or
  Space opens the chat; the open chat carries `aria-current="true"`. The rows
  were `<li>` elements with a click listener, which a keyboard could not reach
  at all, so no conversation could be opened without a mouse or touch.
  Each button is named "name, status" (plus "3 unread messages" when there
  are any): named from its content it read "e2ebobOffline", and the unread
  badge relied on `aria-label` on a plain `<span>`, which ARIA does not allow
  (`tests/js/contact-row.test.mjs`, `tests/test_contact_rows.py`).
  Presence is shown once, as a pip on the avatar coloured by status, beside
  the status in words. A second dot at the row's edge was green for anyone
  not offline, so an "away" contact showed green beside an amber pip. Your
  own avatar shows your initial until there is a photo, as contacts do; it
  was an empty circle (`tests/test_presence_and_own_avatar.py`).
