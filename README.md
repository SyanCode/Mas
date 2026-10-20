# Mas

**Mas maps the server you already have.** Point it at a Debian + nginx box that has been
running for years, with a mix of PHP sites, Node apps under PM2, a few systemd units and a
Docker container or two, and it tells you what is running, where, for which domain, under
which user and from which git repository. Nothing gets reinstalled or broken.

You then manage those services from the `mas` CLI or from a small web dashboard protected
by a password and two-factor authentication, keep an eye on certificates and nginx, and
deploy projects from GitHub on every push, Vercel-style, on the same machine.

> Status: **phase 3**: detection, CLI, dashboard with 2FA, actions and logs, health checks,
> GitHub deployments with releases, rollback and adoption of existing services.

## Why another one?

Coolify, Dokploy and CapRover are great, but they want a clean server: everything has to
run in their containers, behind their proxy. If your server already hosts ten sites
configured by hand, adopting one of them means migrating everything.

Mas starts from the other end:

|                         | Coolify / Dokploy / CapRover | Mas                                   |
|-------------------------|------------------------------|---------------------------------------|
| Starting point          | Fresh server                 | Your existing server                  |
| Reverse proxy           | Traefik / Caddy (theirs)     | Your nginx, your vhosts               |
| Runtimes                | Docker only                  | PHP-FPM, systemd, PM2, Docker, static |
| Existing apps           | Re-deploy them               | Detected and adopted in place         |
| Detection               | n/a                          | Deterministic parsing, no AI          |

Every detection is deterministic: config file parsing, `/proc`, `ss`, `systemctl`, `pm2 jlist`
and the Docker socket. No AI model and no external API are involved.

## Installation

On the server (Debian 12+, as root):

```console
$ mkdir -p /tmp/mas-src && tar -xzf mas.tar.gz -C /tmp/mas-src
                                        # or: git clone https://github.com/<you>/mas /tmp/mas-src
$ cd /tmp/mas-src
$ sudo ./deploy/install.sh
```

The installer is idempotent and asks for two things:

1. **The admin password, then 2FA**: a QR code is printed in the terminal; scan it with
   Google Authenticator, Aegis, 1Password, Bitwarden… and type the 6-digit code.
2. **The dashboard domain** (e.g. `mas.example.com`, its DNS must point to the server). Mas gets
   a Let's Encrypt certificate and installs an HTTPS vhost. Leave it empty to keep the dashboard
   private and reach it through an SSH tunnel:
   `ssh -L 7070:127.0.0.1:7070 you@server`, then open http://localhost:7070.

Non-interactive: `sudo ./deploy/install.sh --domain mas.example.com --email you@example.com`.

What gets installed: `/opt/mas/src` (source), `/opt/mas/venv`, `/etc/mas/config.toml`,
`/var/lib/mas/mas.db`, `mas.service`, `/etc/nginx/sites-available/mas.conf` and `/usr/local/bin/mas`.

### Updating

```console
$ sudo mas self-update                       # git pull (if installed from a clone)
$ sudo mas self-update --from mas.tar.gz     # from a `git archive` tarball
```

It replaces the source (the previous one is kept as `/opt/mas/src.mas-bak-<date>`), reinstalls,
rebuilds the dashboard and restarts `mas.service`.

## CLI

```console
$ sudo mas scan                 # table of every detected service (--json available)
$ sudo mas logs blog.example.com -n 200
$ sudo mas restart api          # asks for confirmation (-y to skip)
$ sudo mas start|stop|reload <service>
$ sudo mas nginx reload         # nginx -t first, reload only if valid
$ sudo mas history              # who did what, when, with what result

$ sudo mas user create [name]   # password + 2FA QR code
$ sudo mas user 2fa [name]      # set up / replace the authenticator app
$ sudo mas user 2fa-reset [name]  # lost phone: disable 2FA (root only)
$ sudo mas user passwd [name]
```

`<service>` is the id shown by `mas scan` (usually the domain), or any unambiguous part of it.

| Manager        | Actions                        | Command                                           |
|----------------|--------------------------------|---------------------------------------------------|
| `systemd`      | start, stop, restart, reload   | `systemctl <action> <unit>`                       |
| `pm2`          | start, stop, restart, reload   | `runuser -u <owner> -- pm2 <action> <app>`        |
| `docker`       | start, stop, restart           | Docker API `POST /containers/<id>/<action>`       |
| `php-fpm`      | reload, restart                | `systemctl <action> php*-fpm.service`             |
| `nginx-static` | reload                         | `nginx -t && systemctl reload nginx`              |

## Dashboard

![Services list placeholder](docs/screenshot-services.png)
![Service page placeholder](docs/screenshot-service.png)

- Services list: status dot, domains (links), type, manager, port, SSL expiry (orange under
  14 days), GitHub repository; filters (with/without domain, issues) and search.
- Service page: details, logs (refresh / auto-refresh), actions with confirmation for stop and
  restart, action history, display name, notes, manual type label.
- Settings: enable / replace / disable 2FA with a QR code, change password.
- Light and dark theme (follows the system by default).

## Health checks

`mas doctor` (CLI) and the **Health** page check, read-only:

| Check | Why it matters |
|-------|----------------|
| Broken certbot lineage (`live/*/cert.pem` not a symlink), renewal config without files | certbot skips or fails on it at every run |
| Expired / expiring certificates | renewal is failing somewhere |
| Certificates used by no enabled vhost | leftovers that make `certbot renew` fail |
| `standalone` renewals with `systemctl stop nginx` hooks | every renewal takes all sites down, and may leave nginx stopped |
| Missing webroot, inactive `certbot.timer` | renewals cannot succeed |
| nginx running outside systemd | `systemctl reload` does nothing, restarts fail with "address already in use" |
| vhost pointing to a missing certificate, `nginx -t` failing | the next reload breaks every site |
| Domains not resolving, or resolving to another server | site moved elsewhere: its vhost/certificate here are dead weight |

Each problem comes with an explanation and, when safe, the command that fixes it. Mas never
applies these fixes by itself.

## Deploying from GitHub

### 1. Connect GitHub (once)

In **Settings → GitHub**, click **Create GitHub App** (optionally for an organization). GitHub
shows the App to create; confirm, and GitHub sends its credentials back to Mas through the
[manifest flow](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest):
App ID, private key (stored in `/etc/mas/github-app.pem`, `0600`) and webhook secret (stored in
`/etc/mas/config.toml`, `0600`). Then click **Install** and choose the repositories Mas may read.

The App is private and asks for the minimum: **Contents: read**, **Metadata: read**,
**Pull requests: read**, and the **push** and **pull_request** webhook events.

<details>
<summary>Creating the App by hand instead</summary>

1. github.com → Settings → Developer settings → GitHub Apps → **New GitHub App**.
2. Homepage URL: `https://<your dashboard>`; Webhook URL: `https://<your dashboard>/api/github/webhook`;
   Webhook secret: a long random string.
3. Permissions: Contents read-only, Metadata read-only, Pull requests read-only. Events: Push, Pull request.
4. "Only on this account", create, then **Generate a private key** (downloads a `.pem`).
5. On the server: `sudo mas github setup` (asks for the App ID, slug, `.pem` path and secret), then
   `sudo systemctl restart mas`, and install the App on your repositories.
</details>

### 2. Create an app

**Apps → New app**: pick a repository and a branch. Mas detects the type (Next.js, Node, Python,
static, PHP) from the repository; set an optional domain and environment variables (stored
encrypted with a key in `/etc/mas/secret.key`). Create: the first deployment starts and its log
streams live in the dashboard.

Every push to the branch then deploys automatically:

1. `git clone` of the pushed commit into `/srv/mas/apps/<app>/releases/<sha>/` (the installation
   token is passed to git through the environment, never on the command line or in logs)
2. build as the dedicated user `mas-<app>` (`npm ci && npm run build`, a Python venv +
   `pip install`, `composer install`, or your own build command)
3. atomic switch of the `current` symlink
4. `mas-app-<app>.service` (generated systemd unit, non-root user, `NoNewPrivileges`,
   `ProtectSystem`, `ProtectHome`, app listening on `127.0.0.1:<port>`) restarted
5. nginx vhost generated from a template (backup, `nginx -t`, reload; refused if another vhost
   already serves the domain)
6. `certbot certonly --nginx` if the domain has no certificate yet, then HTTPS vhost
7. HTTP health check; **if it fails, the previous release is put back automatically**

The last 5 releases are kept; **Rollback** puts any of them back in one click (`mas rollback <app>`).

### Domains (OVH)

Mas can create the DNS records itself. In **Settings → Domains**:

1. Set this server's **public IP** (Mas suggests the address your existing domains point to).
2. Click **Create an API token** (OVH Europe, Canada or US): the OVH page opens with the needed
   rights pre-filled (`GET /me`, `GET/POST/PUT /domain/zone/*`, never `DELETE`). Paste the
   application key, application secret and consumer key; Mas checks them with OVH and stores
   them encrypted.

Then, on an app (or in the *New app* form), pick one of your domains and type a sub-domain
(the app name by default). Mas checks the name live (free, already pointing here, pointing
elsewhere, CNAME, already used by a site), creates the `A` (and `AAAA`) record, refreshes the
zone, waits until the name resolves, then deploys with the nginx vhost and the HTTPS
certificate. A record pointing elsewhere is only replaced if you tick the confirmation; a CNAME
is never touched.

CLI: `mas dns connect`, `mas dns ip <ipv4> [--ipv6 …]`, `mas dns zones`,
`mas domain <app> <sub.example.com> [--overwrite | --no-dns]`.

Without OVH, type the domain by hand and create the `A` record at your DNS provider.

### Adopting an existing service

A detected service whose folder is a GitHub checkout (for example a PM2 or systemd app) shows
**Connect to GitHub** on its page. Mas then deploys it **in place**, without reinstalling anything:
`git fetch` + `git reset --hard` in its folder (as the folder owner, refused if there are local
uncommitted changes), build, restart through its current manager, health check, and
`git reset` back to the previous commit if it fails.

### CLI

```console
$ sudo mas apps                    # apps, live release, last deploy
$ sudo mas deploy <app>            # deploy now, logs printed live
$ sudo mas releases <app>
$ sudo mas rollback <app> [release]
$ sudo mas github status
```

Pull-request preview deployments are not implemented yet; the data model (an `environment`
per app) is ready for them.

## How detection works

```
nginx vhost ──proxy_pass──▶ port ──ss──▶ PID ──/proc──▶ cgroup → manager (systemd / pm2 / docker)
                                                 └────▶ cwd    → project folder → type, git
nginx vhost ──fastcgi_pass + root──▶ PHP-FPM site
nginx vhost ──root only──▶ static site
listening port / pm2 app / container / unit without vhost ──▶ "no domain" service
```

| Collector       | Source                                                                 |
|-----------------|------------------------------------------------------------------------|
| `nginx`         | `/etc/nginx/sites-enabled/*`, `conf.d/*.conf`, includes, upstreams, certificates |
| `ports`         | `ss -tlnpH` (TCP) and `ss -xlnpH` (unix sockets)                       |
| `processes`     | `/proc/<pid>/{status,cmdline,cwd,cgroup,environ}`                      |
| `systemd`       | `systemctl list-units` / `systemctl show`, OS units filtered out       |
| `pm2`           | `pm2 jlist` for every user with a `~/.pm2` (only if its daemon is running) |
| `docker`        | `/var/run/docker.sock` (skipped cleanly if absent)                     |

Certbot's "80 → 443" redirect blocks and `www.` redirects are merged with the HTTPS block of the
same site. Git metadata (remote, branch, last commit) is read directly from `.git/`, with any
credentials embedded in the remote URL stripped.

Service types are decided by ordered rules: `fastcgi_pass` or `.php` files → **php**,
`package.json` with `next` → **nextjs**, `package.json` → **node**,
`requirements.txt` / `pyproject.toml` → **python**, `root` without proxy → **static**,
otherwise **unknown**. A label set by hand in the dashboard always wins.

### Configuration

`/etc/mas/config.toml`:

```toml
[server]
port = 7070           # always bound to 127.0.0.1
session_hours = 12    # idle timeout (sessions also expire after 7 days)
scan_interval = 60    # background scan, seconds

[scan]
extra_exclude_units = ["my-backup-agent"]  # added to the built-in OS list
hide_os_daemons = true  # hide units with no port and only system paths in their command
```

## Security model

- **Network**: the API listens on `127.0.0.1:7070` only (`mas serve` refuses anything else).
  The public entry point is the nginx vhost: HTTPS only, HSTS, `X-Frame-Options: DENY`, CSP,
  `client_max_body_size 2m`.
- **Authentication**: a single admin account created on the server (`mas user create`), never
  from the web. Passwords are hashed with argon2id. **TOTP 2FA** (RFC 6238, 30 s, ±1 step), and
  each code is accepted only once (replay protection). Five failed logins per account or ten per
  IP in 15 minutes trigger a temporary block (HTTP 429). Logins are written to the audit log.
- **Sessions**: random 256-bit token in a `HttpOnly`, `Secure`, `SameSite=Strict` cookie; only
  its SHA-256 is stored. 12 h idle timeout, 7 days maximum. Changing the password or the 2FA
  device signs out every other session.
- **CSRF**: every mutating request needs the per-session `X-CSRF-Token` header, and a foreign
  `Origin` is rejected.
- **Deployments**: the GitHub webhook is rejected unless its `X-Hub-Signature-256` HMAC is valid,
  before the payload is even parsed. Apps run as their own non-root user, listen on loopback, and
  get their secrets from a `0600` environment file; secrets are encrypted at rest in the database.
  Build commands you configure run as that unprivileged user. Generated files are backed up
  before being replaced and nginx is only reloaded after a successful `nginx -t`.
- **Actions**: strict allow-list `(manager, action) → argv`, built from identifiers found by
  the scan (re-validated with regexes), executed without a shell. Stop and restart ask for
  confirmation. Every action, with its command and output, is written to the audit log.
- **nginx**: Mas only reloads after a successful `nginx -t`. The installer backs up any file it
  replaces (`<file>.mas-bak-<timestamp>`) and restores it if `nginx -t` fails.
- **Read-only discovery**: scans never write. The test suite hashes the whole fixture tree
  before and after a scan and allow-lists every command executed. `pm2 jlist` is only called
  when the user's daemon is already running (it would otherwise start one), and as that user.
- **Secrets** found along the way (credentials in git remotes, PM2 environment variables) are
  neither stored nor displayed. The database (`/var/lib/mas/mas.db`) is `0600`, root only.
- **Lost phone**: `sudo mas user 2fa-reset` on the server. Root on the server is the
  recovery path.
- **Why the agent runs as root**: it must read `/proc` for every user, `ss -p` and the Docker
  socket, and restart services. `mas.service` reduces the blast radius with
  `NoNewPrivileges=yes`, `ProtectSystem=full`, `PrivateTmp=yes`, `ProtectKernel*`, and
  `ReadWritePaths=` limited to `/var/lib/mas /srv/mas /etc/nginx /etc/mas /etc/systemd/system
  /etc/letsencrypt`.

## Development

```console
$ python -m venv .venv && .venv/bin/pip install -e ".[dev]"
$ .venv/bin/pytest
$ cd web && npm ci && npm run build      # dashboard → web/out
```

The tests don't need a real server. `tests/fixtures/server.py` builds a fake Debian tree (nginx
configs, `/proc`, `ss` and `systemctl` outputs, a PM2 jlist, Docker API responses), and
`FixtureHost` serves it to the collectors. You can run the whole app against it:

```console
$ python tests/fixtures/server.py /tmp/mas-demo
$ mas --fixture /tmp/mas-demo --db /tmp/mas-demo.db user create admin
$ mas --fixture /tmp/mas-demo --db /tmp/mas-demo.db serve --insecure-cookie
```

## Roadmap

- [x] **Phase 1: detection.** Collectors, correlation, type detection, `mas scan [--json]`.
- [x] **Phase 2: dashboard and management.** API, 2FA, background scan, logs, allow-listed
      actions, labels, audit log, `install.sh`, `mas.service`, `mas self-update`.
- [x] **Phase 2.5: health checks.** Certificates, certbot renewal, nginx, DNS (`mas doctor`).
- [x] **Phase 3: deploy from GitHub.** GitHub App (manifest flow), signed webhooks, atomic
      releases, generated systemd unit and nginx vhost, certbot, health check with automatic
      rollback, 5 releases kept, live build logs (SSE), adoption of detected services.
- [ ] Later: preview deployments per pull request, recovery codes for 2FA, notifications.

## License

MIT
