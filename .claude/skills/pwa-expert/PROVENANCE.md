# Provenance

This skill is vendored, not written for this project.

| | |
|---|---|
| Source | https://github.com/curiositech/some_claude_skills |
| Path in source | `.claude/skills/pwa-expert` |
| Licence | MIT, Copyright (c) 2025 Erich Owens (see `LICENSE` in this folder) |
| Vendored on | 10 October 2026 |
| Covers | Service workers, caching strategies, install prompts, offline handling, background sync, update flow |

## Why it is checked in rather than installed per-developer

Same reason as `../owasp-security/PROVENANCE.md`: a final-year project is
marked on what is in the repository, so the guidance the mobile work was built
against is versioned beside the code it produced.

## What was checked before vendoring

Guidance text only: no scripts, no network calls, no build steps. Scanned for
embedded commands, downloads and credential handling before copying.

One thing to know when reading it: the reference files are written in React and
Next.js. Reveal-X is Flask with plain JavaScript, so the patterns were
translated rather than copied. `references/nextjs-integration.md` does not apply
to this project at all and is kept only so the skill is intact for future use.

## What was implemented from it

- `app/static/js/pwa.js` — `beforeinstallprompt` capture, install button,
  `appinstalled` handling, iOS Add-to-Home-Screen hint, update banner wiring
- `app/static/sw.js` — `SKIP_WAITING` message handler; `skipWaiting()` removed
  from the install handler so an update cannot reload the page unprompted
- `app/static/manifest.json` — `id`, `scope`, `lang`, `dir`, `categories`,
  two home-screen shortcuts
- `app/static/css/style.css` — `100dvh` with a `100vh` fallback, safe-area
  insets, banner styles
- `app/templates/index.html` — install button, update banner, iOS hint
- `tests/test_pwa.py` — 12 tests covering all of the above

## Updating

```
git clone --depth 1 https://github.com/curiositech/some_claude_skills.git
cp -r some_claude_skills/.claude/skills/pwa-expert .claude/skills/
```

Re-read `SKILL.md` and `references/` after any update, for the reason above.
