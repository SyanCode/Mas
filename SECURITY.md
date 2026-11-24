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

## Economy mode

Economy mode (`/api/resources/auto`, `mas economy`) changes the memory limits and the sleep of every app, so only an
administrator signed in can turn it on or off: API tokens are refused. It only adds a limit to an app without one,
raises a limit an app is close to, or adds sleep to an app without visits; it never lowers or removes a value a
person set.

Capping a container (`PUT /api/containers/<name>/memory`, or the economy mode with `--shrink`) only runs
`docker update --memory` on a running container Mas lists, with a value between 64 MB and 64 GB; it is in the history.
Lowering a limit set by a person, or capping containers, happens only after an administrator ticked the agreement box.

## Auto-prompting

Improving a request sends the request, the answers to its questions and the list of the sites (id, domain, type,
repository name) to the AI account: no file, log, variable or secret. Its answer is read as JSON, cut to size, and only
shown: nothing starts until the administrator clicks. `/api/robot/refine` refuses API tokens.

## GitLab

The GitLab access token is kept encrypted with the key of the app variables, sent only to that GitLab's API, and to
git through `GIT_CONFIG_*` environment variables for that address only (never in a command line or a log). A GitLab
on the internet must be reached over https; plain http is accepted only for this machine or a private network.
Connecting asks the administrator's password; `/api/gitlab` refuses API tokens. The webhook checks GitLab's secret
token (constant-time compare) and refuses a replayed delivery. Merge requests from a fork never get a preview.
The copies of a GitLab app on other servers of the cluster receive this token for their clone, as copies of a GitHub
app receive a clone token: a cluster server is trusted like this one. Prefer a token limited to the projects Mas
deploys (a group access token) over a personal one.

The **GitLab Runner** of the catalog gets the Docker socket of the server: it starts each CI job in a container of
its own, which means the pipelines of the projects it serves can start containers on this server. Register it only
on a GitLab whose projects you trust (your own), keep job containers unprivileged (the default), and use a separate
server for untrusted code. The nightly archive of a GitLab installed with the package holds `gitlab-secrets.json`: it
is written readable by root only and sent only to the remote storage you configured for Mas's backups.

## Other distributions and web servers

On SELinux systems the installer turns on `httpd_can_network_connect` (nginx may connect to local ports, the apps)
and labels `/srv/mas` as web content; on firewalld systems it opens the `http` and `https` services. Apache and
Caddy configurations are only read; their reload runs their own configuration test first (`apachectl configtest`,
`caddy validate`) and nothing is reloaded if it fails.

Behind Caddy or Apache (`mas front`), nginx listens on 127.0.0.1 only and trusts `X-Forwarded-For` (the visitor's
address) and `X-Forwarded-Proto` only from 127.0.0.1 and ::1, the front server. `mas.service` may write in
`/etc/caddy`, `/etc/apache2` and `/etc/httpd` for that (its own files only); switching is a root command.
`/api/front` refuses API tokens.
