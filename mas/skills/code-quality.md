---
name: code-quality
description: Repository hygiene that separates a maintained project from a generated one: structure, naming, dead code, errors and states, types, configuration, README, dependencies, commits. Load it when you change code or when the goal is to clean up a project.
---

# Code quality

Change only what the goal needs, in the style of the repository. Read the existing conventions (formatter, lint
config, folder layout, naming) and follow them.

- **Structure:** components and modules split by responsibility (no 800-line file); shared UI in one place; one
  styling approach; configuration in one place.
- **Names:** say what things are (`OpeningHours`, not `Section3`; `formatPrice`, not `helper2`). Same concept, same
  name everywhere. Files named consistently (kebab-case or PascalCase, not both).
- **Dead code:** no commented-out blocks, no unused files, exports, dependencies, demo pages or assets of the
  scaffold. No `console.log`, `debugger`, `alert()` in shipped code.
- **States:** every async view handles loading, empty, error and success. Errors are shown to the user in words and
  logged for the developer; nothing fails silently.
- **Types and validation:** TypeScript strict where the project uses TS; no `any` without a reason; inputs validated
  at the edge (forms, API routes).
- **Security basics:** no secret in the repository or in client code; user input escaped; forms protected against
  spam (honeypot, rate limit); external links with `rel="noopener"`.
- **Configuration:** `.env.example` lists every variable (names only) with a comment; `package.json` scripts `dev`,
  `build`, `start`, `lint` work.
- **README:** what the project is, how to run it locally, how it is deployed (Mas), where content is edited. Not the
  framework's bootstrap text.
- **Dependencies:** no unused ones, no duplicates doing the same job, lock file committed.
- **Commits:** one clear subject line in the imperative, describing the change ("Add opening hours to the footer"),
  not "update" or "fix stuff".
