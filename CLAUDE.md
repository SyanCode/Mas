# Working on Mas

## Rules from the owner

- **Every commit is by `SyanCode <hilairebenamorsyan@gmail.com>` alone**: no `Co-Authored-By`, no session or
  "generated with" lines, in commits and pull request texts. `git config user.name/user.email` are set to that.
- **Everything goes to `main`**, on GitHub there is no other branch. Commit on `main`, push `origin main`.
  Never force-push without being told to.
- Answer the owner in French; code, comments, commit messages and UI source strings are in English.

## At the end of every piece of work (do all of it, in this order)

1. `python -m pytest tests -q -x --ignore=tests/e2e` passes (about 3 minutes; run it in the foreground with a long timeout).
2. Anything under `web/` changed: `cd web && npx tsc --noEmit` is clean, and every new `t("…")` text exists in
   `web/locales/{fr,es,de}.json` (`tests/test_i18n.py` checks it).
3. A new route that returns or changes secrets, accounts or server settings is added to `API_TOKEN_FORBIDDEN`
   in `mas/auth.py` (API tokens and read-only accounts never reach it) and has a test saying so.
4. The README roadmap / CLI list and `SECURITY.md` mention the feature when users need to know about it.
5. `git add -A && git commit` (one clear English subject line, no trailers) then `git push origin main`.
6. Check: `git ls-remote --heads origin` shows `main` only, and `git log --format=%an | sort -u` shows `SyanCode` only.
7. Report to the owner in French: what exists now, what was verified (tests, tsc), what was **not** verified
   (anything needing a real server, GitHub or a browser), the limits, and the next step.
8. Pick the next improvement yourself when told to go on: security and data-loss risks first, then
   operations, then polish.

## Layout

`mas/` Python (FastAPI API, CLI `mas`), `web/` Next.js dashboard (static export), `tests/` pytest,
`tests/debian/run.sh` the Debian check, `deploy/` installer and units.
