# Mas

**Mas maps the server you already have.** Point it at a Debian + nginx box that has been
running for years, with a mix of PHP sites, Node apps under PM2, a few systemd units and a
Docker container or two, and it tells you what is running, where, for which domain, under
which user and from which git repository. Nothing gets reinstalled or broken.

You then manage those services from the `mas` CLI or from a small web dashboard protected
by a password and two-factor authentication. Deploying new projects from GitHub,
Vercel-style, is next on the roadmap.

> Status: **phase 2**: detection, CLI, dashboard with 2FA, start/stop/restart/reload, logs.

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
- [ ] **Phase 3: deploy from GitHub.** GitHub App, signed webhooks, atomic releases in
      `/srv/mas/apps/<app>/releases/<sha>`, generated systemd unit and nginx vhost, certbot,
      healthcheck with automatic rollback, the last 5 releases kept, build logs over SSE, and
      adoption of already-detected services that have a GitHub remote.
- [ ] Later: preview deployments per pull request, recovery codes for 2FA.

## License

MIT
