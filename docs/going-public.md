# Going Public: Pre-Push Content Review

This repo is **public** (`github.com/Riclex/lineata`). Before any push to `origin`,
run a content review of every tracked file — not just a secret scan. "Already
tracked" and "published content" are **not** the same as "safe to be public."

This checklist exists because of a real recurring failure: private content was
pushed three times (an internal goal doc, a README monetization section, and
paid Substack articles) before the pattern was caught. The cheap fix is a
pre-push step, not a post-push history rewrite.

## The principle

A secret scan (`grep` for `.env`, tokens, credentials) catches *secrets*. It
does **not** catch:

- **Internal docs** — strategy, roadmaps, planning notes, working research.
- **Paid content** — articles published behind a paywall (Substack, etc.).
- **Personal content** — branding notes, private brainstorming, contact info.

All three are private. Review for them the way the test suite reviews for
correctness — the privacy review is as important as the verification gate.

## Known-private paths (gitignored — never re-add)

These are kept locally only. They are in `.gitignore`, so `git add` skips them.
**Never** use `git add -f` on them, and never remove them from `.gitignore`:

- `InvestmentExecutionDatabase-goal.md` — product vision/strategy (+ branding notes)
- `docs/project-roadmap.md` — detailed roadmap
- `docs/superpowers/` — SDD design/plan docs
- `research/` — raw research/extraction notes
- `articles/` — published analyses (paid Substack content)

If you add a new private file locally, add it to `.gitignore` in the same change.

## Pre-push walkthrough

Before `git push` to `origin`:

1. **List what would go up.** `git ls-files` (tracked) and `git status`
   (new/modified). Walk every path and ask, for each: *Is this internal? Paid?
   Personal?* If yes to any, it must be gitignored and removed from the commit
   (and from history if it was ever pushed).
2. **Secret scan.** `git grep -nEi "password|secret|token|api[_-]?key|BEGIN .*PRIVATE KEY" -- .`
   Secrets must never be tracked.
3. **Content scan (human).** Skim any doc/code that touches strategy,
   monetization, pricing, personal notes, or unpublished article text. A grep
   can't catch these — a human pass does. Pay attention to README sections,
   goal/vision docs, roadmap text, and anything that reads like article prose.
4. **Check `.gitignore` covers the private paths** above (and any new private
   file added locally in this session).
5. **Confirm the remote is the public one** (`git remote -v`) and that you're
   pushing the intended branch.

## If private content was already pushed

Deleting it going forward is not enough — old commits still contain it, and
anyone can clone. Rewrite history with `git filter-repo` (purge the paths with
`--invert-paths --path`; strip leaked sections from surviving files with
`--blob-callback`), then `--force` push. This is a blunt, risky instrument — the
checklist above is the cheaper fix. After a rewrite, verify with a fresh clone:
`git log --all -p | grep -c "<private-string>"` should be `0`.