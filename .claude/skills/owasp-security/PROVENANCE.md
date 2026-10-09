# Provenance

This skill is vendored, not written for this project.

| | |
|---|---|
| Source | https://github.com/agamm/claude-code-owasp |
| Path in source | `.claude/skills/owasp-security` |
| Licence | MIT (see `LICENSE` in this folder) |
| Vendored on | 10 October 2026 |
| Standards covered | OWASP Top 10:2025, ASVS 5.0, OWASP Top 10 for LLM Applications (2026), OWASP Top 10 for Agentic Applications (2026) |

## Why it is checked in rather than installed as a plugin

The upstream repository offers a plugin install (`/plugin install owasp-security@agamm`),
which keeps the skill in the developer's own Claude Code profile. That is fine for one
machine, but a final-year project is marked on what is in the repository. Vendoring the
skill means the review criteria used on this codebase are versioned alongside it and a
marker can read them.

## What was checked before vendoring

The content is guidance text only: no scripts, no network calls, no build steps. Every
external URL in it points to owasp.org, genai.owasp.org, cheatsheetseries.owasp.org,
cwe.mitre.org, csrc.nist.gov, pages.nist.gov, registry.npmjs.org or github.com. It was
read before being copied in, because a skill file steers how an assistant behaves and is
therefore worth the same scrutiny as a dependency.

## Updating

```
git clone --depth 1 https://github.com/agamm/claude-code-owasp.git
cp -r claude-code-owasp/.claude/skills/owasp-security .claude/skills/
```

Re-read `SKILL.md` and the `reference/` files after any update, for the reason above.
