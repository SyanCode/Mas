# Security policy

Mas runs as root and manages nginx, systemd, certificates and DNS records, so security
reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository.

Include what you can: affected version (`mas --version`), steps to reproduce, impact. You will
get an answer as soon as possible; fixes are released with a note in the changelog once users
have had a chance to update.

## Supported versions

Mas is in beta: only the latest release receives security fixes.

## Scope

In scope: the API and dashboard, authentication (passwords, TOTP and recovery codes, sessions, CSRF, read-only accounts), CMS editor accounts and their permissions per site (and the read-only "view as" of an administrator), preview deployments, hosted files and their download links (`/d/<id>`), the backups of Mas and their S3 copy, the
deployment pipeline, the generated nginx/systemd configuration, the GitHub webhook, the DNS
provider integrations and the installer. The [security model](README.md#security-model)
describes what Mas is designed to protect against.

## Dependencies of the hosted apps

Mas checks the packages installed with each app against the public [OSV](https://osv.dev) database every day
and after each release (`mas deps`, the **Dependencies** page). Only package names and versions are
sent to `api.osv.dev`, never code, files or domains; development packages are not sent. Turn it off with
`mas deps --off` (only an administrator signed in, never an API token, can change it). A vulnerable package is
reported in Health and by the server alerts with the version to upgrade to.

Mas then upgrades it by itself (`mas deps --auto push`, the default; `pr` for a pull request, `off` to only be
told). The new lock file is computed with `npm --package-lock-only --ignore-scripts` as the user `nobody`, in a
temporary folder holding only `package.json` and `package-lock.json` (no `.npmrc`, no secret, no code of the app):
no package is installed and no package script runs at that step. The commit goes through the GitHub App on the
deployed branch, so it is built by the usual pipeline as the app's own user; a failed release is undone by a second
commit. Only security fixes inside the same major version are made. Changing this mode needs an administrator
signed in, never an API token. The vhosts Mas writes hide the nginx version and the `X-Powered-By` header,
but a framework can still print its version in its own files: keeping the packages up to date is the fix.

## The robot's skills

The robot's skills (`mas skills`, `mas/skills/*.md`) are plain text shipped with Mas: loading one reads a file of
Mas, never of a site, and the name must be one of the listed skills. `polish_report` only reads the text files of
an app's repository through the GitHub App (the same blocked paths as the robot: no `.env`, keys or `.github`) and
treats them as data. `install_skills` commits only those skill files, under `.claude/skills/`, and like every other
commit of the robot waits for a click in *Ask me* mode. The MCP tools `list_skills` and `get_skill` read nothing
else and need no write access.

## AI budget and questions

A question asked to the robot mascot runs with the read-only tools only: the model is never offered a tool that
commits, deploys, rolls back or restarts, and such a call is refused if it is attempted. The monthly token cap is
checked before every request to an AI provider; only an administrator signed in can change it (`/api/robot` refuses
API tokens).
