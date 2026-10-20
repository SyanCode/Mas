# Mas

**Mas maps the server you already have.** Point it at a Debian + nginx box that has been
running for years, with a mix of PHP sites, Node apps under PM2, a few systemd units and a
Docker container or two, and it tells you what is running, where, for which domain, under
which user and from which git repository. Nothing gets reinstalled or broken.

Then it lets you manage those services from a small web dashboard, and (soon) deploy new
projects from GitHub, Vercel-style, on the same machine.

> Status: **phase 1 (read-only detection)**. The dashboard and deployments are on the roadmap below.

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
otherwise **unknown** (you can label it by hand, and the manual label wins).

## Usage

```console
$ sudo mas scan          # table
$ sudo mas scan --json   # machine-readable
```

Run it as root: reading other users' `/proc` entries and `ss -p` requires it. As a regular user
you get a partial view and warnings.

```
 ● app.example.com   app.example.com   nextjs  pm2          3000  2000  alice  /home/alice/apps/web  2027-01-15  gh:acme/web@production
 ● blog.example.com  blog.example.com  php     php-fpm      -     900   -      /var/www/blog/public  2026-10-30  gh:acme/blog@main
 ● mariadb           no domain         unknown systemd      3306  800   mysql  /var/lib/mysql        -           -
```

![Dashboard screenshot placeholder](docs/screenshot-dashboard.png)
*(Screenshots will come with the dashboard in phase 2.)*

### Configuration

`/etc/mas/config.toml` (optional):

```toml
[scan]
# Added to the built-in list of OS units (ssh, cron, nginx, systemd-*, …)
extra_exclude_units = ["my-backup-agent", "tailscaled"]
# Or replace the built-in list entirely:
# exclude_units = ["ssh", "nginx"]
```

## Installation

> Phase 1 has no installer yet. `deploy/install.sh` comes with phase 2.

```console
$ git clone https://github.com/<you>/mas.git /opt/mas-src
$ python3 -m venv /opt/mas && /opt/mas/bin/pip install /opt/mas-src
$ sudo /opt/mas/bin/mas scan
```

## Security model

- **Phase 1 is strictly read-only.** Mas reads files and runs `ss`, `systemctl list-units/show`,
  `pm2 jlist` and a Docker `GET /containers/json`. It never writes, reloads or restarts anything.
  The test suite checks this: it hashes the whole fixture tree before and after a scan and
  allow-lists every command executed.
- `pm2 jlist` would start a PM2 daemon if none is running, so Mas only calls it when the
  user's daemon is alive, and runs it as that user (`runuser -u <user>`), never as root.
- Commands always run with an argument list, never `shell=True`.
- Secrets found along the way (credentials in git remotes, PM2 environment variables) are not
  kept or displayed.
- Coming with phase 2: the API listens on `127.0.0.1:7070` only, behind nginx + HTTPS. There is a
  single admin account (argon2), `HttpOnly`/`Secure`/`SameSite=Strict` session cookies, CSRF
  protection and login rate limiting. Actions come from a strict allow-list `(manager, action) → argv`
  and are logged in the database. Any nginx change is backed up (`<file>.mas-bak-<timestamp>`) and
  validated with `nginx -t` before a reload.
- **Why the agent runs as root**: it must read `/proc` for every user, `ss -p`, and the Docker
  socket. `mas.service` reduces the blast radius with `NoNewPrivileges=yes`, `ProtectSystem=full`,
  `PrivateTmp=yes` and `ReadWritePaths=` limited to the directories Mas manages.

## Development

```console
$ python -m venv .venv && .venv/bin/pip install -e ".[dev]"
$ .venv/bin/pytest
```

The tests don't need a real server. `tests/fixtures/server.py` builds a fake Debian tree (nginx
configs, `/proc`, `ss` and `systemctl` outputs, a PM2 jlist, Docker API responses), and
`FixtureHost` serves it to the collectors. You can scan it too:

```console
$ python tests/fixtures/server.py /tmp/mas-demo
$ mas scan --fixture /tmp/mas-demo
```

## Roadmap

- [x] **Phase 1: detection.** Collectors, correlation, type detection, `mas scan [--json]`.
- [ ] **Phase 2: dashboard and management.** FastAPI on `127.0.0.1:7070`, background scan every 60 s,
      logs (journalctl / pm2 / docker / nginx), start/stop/restart/reload from an allow-list,
      manual labels, safe `nginx -t && reload`, auth, a static Next.js dashboard with dark mode,
      `install.sh`, `mas.service`, `mas self-update`.
- [ ] **Phase 3: deploy from GitHub.** GitHub App, signed webhooks, atomic releases in
      `/srv/mas/apps/<app>/releases/<sha>`, generated systemd unit and nginx vhost, certbot,
      healthcheck with automatic rollback, the last 5 releases kept, build logs over SSE, and
      adoption of already-detected services that have a GitHub remote.
- [ ] Later: preview deployments per pull request.

## License

MIT
