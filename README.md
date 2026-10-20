<div align="center">

# Mas

**Your server, mapped and deployed.**
Self-hosted dashboard that adopts what already runs on your Debian + nginx box, then deploys from GitHub on every push.

![status: beta](https://img.shields.io/badge/status-beta-8b5cf6)
![license: MIT](https://img.shields.io/badge/license-MIT-0ea5e9)
![python 3.11+](https://img.shields.io/badge/python-3.11%2B-22c55e)
![Debian + nginx](https://img.shields.io/badge/Debian%20%2B%20nginx-supported-f59e0b)

![Mas dashboard: an app with its production deployment and live build log](docs/app.jpg)

</div>

**Mas maps the server you already have.** Point it at a Debian + nginx box that has been
running for years, with a mix of PHP sites, Node apps under PM2, a few systemd units and a
Docker container or two, and it tells you what is running, where, for which domain, under
which user and from which git repository. Nothing gets reinstalled or broken.

You then manage those services from the `mas` CLI or from a web dashboard protected by a
password and two-factor authentication, keep an eye on certificates and nginx, link domains
from your registrar (OVH, Cloudflare, Gandi, Hetzner, DigitalOcean, Porkbun) and deploy
projects from GitHub on every push, Vercel-style, on the same machine.

> [!WARNING]
> **Beta.** Mas runs as root and edits nginx, systemd and DNS. Every change is backed up and
> validated (`nginx -t`) and discovery is read-only, but try it on a server you can restore
> first, and read the [security model](#security-model). Feedback and issues are very welcome.

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
and the Docker socket. No AI model is involved anywhere, and discovery needs no external API
(only the optional GitHub and DNS-provider integrations talk to their own APIs).

## Installation

On the server (Debian 12+, as root):

```console
$ git clone https://github.com/SyanCode/Mas.git /opt/mas-install
$ cd /opt/mas-install
$ sudo ./deploy/install.sh --domain mas.example.com --email you@example.com
```

Without `--domain` the dashboard stays private (SSH tunnel, see below). Updates:
`sudo mas self-update` (git pull, reinstall, rebuild, restart).

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
$ sudo mas self-update                       # follow the published branch (git checkout)
$ sudo mas self-update --from mas.tar.gz     # from a `git archive` tarball
```

It updates the source, reinstalls, rebuilds the dashboard and restarts `mas.service`. A git
checkout is moved to `origin/<branch>` even if that branch was force-pushed; it refuses to touch a
checkout with local changes. An archive install keeps the previous source as
`/opt/mas/src.mas-bak-<date>`.

When a newer version is published, every `sudo mas …` command starts with a one-line reminder to
run `sudo mas self-update` (checked against GitHub at most every 6 hours, 2 s timeout, silent when
offline; `MAS_NO_UPDATE_CHECK=1` turns it off).

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

![Overview: every service detected on the server](docs/overview.jpg)

Press <kbd>⌘K</kbd> / <kbd>Ctrl</kbd>+<kbd>K</kbd> anywhere to jump to a service, an app or a page.

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

The App is private and asks for: **Contents: read and write** (write is only used by the CMS, to
commit content edits), **Metadata: read**, **Pull requests: read**, and the **push** and
**pull_request** webhook events. An App created before the CMS has Contents read-only: change it
to *Read and write* in the App settings on GitHub, then accept the new permissions on the
installation.

<details>
<summary>Creating the App by hand instead</summary>

1. github.com → Settings → Developer settings → GitHub Apps → **New GitHub App**.
2. Homepage URL: `https://<your dashboard>`; Webhook URL: `https://<your dashboard>/api/github/webhook`;
   Webhook secret: a long random string.
3. Permissions: Contents read and write, Metadata read-only, Pull requests read-only. Events: Push, Pull request.
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
3. atomic switch of the `current` symlink (a Next.js app built with `output: 'export'` has no
   server: Mas serves its `out/` folder with nginx and skips the systemd unit)
4. `mas-app-<app>.service` (generated systemd unit, non-root user, `NoNewPrivileges`,
   `ProtectSystem`, `ProtectHome`, app listening on `127.0.0.1:<port>`) restarted
5. nginx vhost generated from a template (backup, `nginx -t`, reload; refused if another vhost
   already serves the domain)
6. `certbot certonly --nginx` if the domain has no certificate yet, then HTTPS vhost
7. HTTP health check; **if it fails, the previous release is put back automatically**

The last 5 releases are kept; **Rollback** puts any of them back in one click (`mas rollback <app>`).

### Domains (OVH, Cloudflare, Gandi, Hetzner, DigitalOcean, Porkbun)

Mas can create the DNS records itself. In **Settings → Domains**:

1. Set this server's **public IP** (Mas suggests the address your existing domains point to).
2. Connect one or more registrars. Each card links to the provider's token page and lists the
   exact rights to give (read zones, create/update records; Mas never deletes a record). Keys are
   verified with the provider, then stored encrypted.

| Provider | Credentials |
|----------|-------------|
| OVHcloud | application key + secret + consumer key (token page with pre-filled rights) |
| Cloudflare | API token, template “Edit zone DNS” (records are created DNS-only, grey cloud) |
| Gandi | Personal access token (LiveDNS) |
| Hetzner DNS | API token |
| DigitalOcean | API token with the `domain` scope |
| Porkbun | API key + secret (enable API access on each domain) |

Namecheap is not supported: its API needs a whitelisted IP and rewrites the whole zone on every change.

Then, on an app (or in *New app*), pick one of your domains and a sub-domain (the app name by
default). Mas checks the name live (free, already pointing here, pointing elsewhere, CNAME,
already used), creates the `A` (and `AAAA`) record, waits until it resolves, then deploys with the
nginx site and the HTTPS certificate. A record pointing elsewhere is only replaced after an explicit
confirmation; a CNAME is never touched.

CLI: `mas dns connect <provider>`, `mas dns ip <ipv4> [--ipv6 …]`, `mas dns zones`,
`mas domain <app> <sub.example.com> [--overwrite | --no-dns]`.

### Self-hosted Supabase

**Databases → Deploy Supabase** installs the official Supabase docker stack (Postgres, Auth,
REST, Realtime, Storage, Studio) on the server:

- every published port is bound to `127.0.0.1` (Docker would otherwise bypass the firewall);
- only the public API (`/rest`, `/auth`, `/realtime`, `/storage`, `/functions`, `/graphql`) is
  published through nginx, on the domain you pick (DNS record created like for apps), with HTTPS;
- **Studio and pg-meta stay private**: Mas shows the SSH tunnel command and the Studio login;
- secrets (Postgres password, JWT secret, `anon` / `service_role` keys) are generated by Mas and
  stored encrypted;
- a `pg_dump` runs every day, the last 7 are kept in `/var/lib/mas/backups/supabase`.

On an app, **Connect Supabase** adds `SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, the anon and
service_role keys and `DATABASE_URL` to its (encrypted) environment and redeploys. One instance
per server; the stack needs Docker with the compose plugin.

**Low-memory profile.** The full official stack uses about 2 GB of RAM. Mas only runs what you
tick, and you can change it later from the Databases page (`docker compose up -d --remove-orphans`,
data volumes are kept):

| Component | Default | ≈ RAM |
|---|---|---|
| Postgres, Auth, REST, Kong (always on) | on | 400 MB |
| Studio + pg-meta | on (start/stop on demand) | 250 MB |
| Realtime | on | 150 MB |
| Storage | on | 80 MB |
| Image transformations (imgproxy) | off | 60 MB |
| Edge Functions | off | 150 MB |
| Logs & analytics (Logflare + Vector) | off | 600 MB |
| Connection pooler (Supavisor) | off: Postgres is published directly on `127.0.0.1` | 150 MB |

The services that stay are tuned for a small server: Postgres `shared_buffers=64MB` and smaller
work memory, one Kong worker with a 32 MB cache, a capped Node.js heap for Studio, Storage and
pg-meta. The unmodified upstream file is kept as `docker-compose.original.yml`; the page shows
the memory actually used by each container.

Restore a backup: `docker cp <file> supabase-db:/tmp/r.dump && docker exec supabase-db pg_restore
-U postgres -d postgres --clean /tmp/r.dump`.

### CMS for your clients

Host client sites with Mas and let each client edit their own content, without giving them access
to the server or the code. The CMS is git-based: the repository declares what can be edited, the
client fills a form in the dashboard, and Mas commits the change to the deployed branch, which
redeploys the site.

1. Add `mas.cms.json` at the root of the repository. Each page is one JSON file your site reads at
   build time (`import home from "./content/home.json"`, `fetch`, a static-site generator data
   file…). The **CMS** card of the app can write it for you: it lists the JSON content files of
   the repository (tooling files such as `package.json` or `tsconfig.json` are ignored), guesses a
   field for each value (text, long text, markdown, image path, color, date, email, link, number,
   yes/no, lists of objects), shows what it left out (nested objects, lists of plain values), and
   commits the file once you have reviewed it. By hand, it looks like this:

   ```json
   {
     "pages": [{
       "id": "home", "label": "Home page", "file": "content/home.json",
       "fields": [
         { "key": "title", "type": "text", "label": "Title", "max": 80, "required": true },
         { "key": "intro", "type": "markdown", "max": 2000 },
         { "key": "hero", "type": "image", "dir": "public/uploads", "url": "/uploads", "max_kb": 2048 },
         { "key": "phone", "type": "text", "max": 20 },
         { "key": "services", "type": "list", "item_label": "Service", "max_items": 8,
           "fields": [
             { "key": "name", "type": "text", "required": true },
             { "key": "price", "type": "number", "min": 0 }
           ] }
       ]
     }]
   }
   ```

   Field types: `text`, `textarea`, `markdown`, `number` (`min`, `max`, `integer`), `boolean`,
   `select` (`options`), `url`, `email`, `date`, `color`, `image` (`dir` in the repository, `url`
   as the site serves it, `max_kb`) and `list` (of the other types, `min_items`, `max_items`).
   Every field takes `label`, `help`, `required` and `placeholder`.
2. Give your client an account, in **Settings → People**, one of three ways:
   - **Invitation link** (simplest): pick the sites, send the link. It works once, for 7 days,
     even when sign-up is closed; the client chooses a username and a password and lands on
     their site. Only a hash of the link is stored, and pending links can be revoked.
   - **Open sign-up**: anyone can create an account at `/signup/`, optionally only with an
     invitation code you set. A new account sees nothing until you give it a site. Sign-ups are
     limited to 5 per hour per address and written to the audit log.
   - **New editor**: you choose the username, Mas generates a password shown once.

   **Sites** on each editor chooses which apps it may edit; the app page has the same switch in
   its **CMS** card, which also checks the schema.
3. The client signs in to the same dashboard and only sees **My sites** and **Account** (password,
   2FA). Every other API route answers `403` for an editor.

What keeps the site safe: the schema is always read from the repository (never from the browser);
only the declared fields of the declared JSON files are written, and keys the schema does not know
are kept untouched; values are checked server-side (length, type, allowed choices, links limited
to `https://`, `/`, `mailto:` and `tel:`, no control characters); `.git/`, `.github/` and
`mas.cms.json` itself cannot be targeted; images are checked by their content (PNG, JPEG, GIF,
WebP, AVIF; no SVG) and get a fresh file name. Each save is a single commit (`cms(home): …
Edited by <editor>`) on top of the version the editor opened: if someone pushed meanwhile, the
editor is asked to reload instead of overwriting. Undo a change with `git revert` or roll back
the deployment in Mas.

### Load balancing

The **Load balancing** card of a Node.js, Next.js or Python app spreads its traffic:

- **Instances on this server**: Mas runs the app N times (`mas-app-<id>.service`,
  `mas-app-<id>.2.service`…, each on its own loopback port) to use every CPU core. A redeploy
  restarts them one at a time and checks each one before the next, so the site never goes down;
  if an instance does not come back, the release is rolled back.
- **Other servers**: `host:port` of copies of the app you run elsewhere, typically over a
  private network. They are added to the same pool.

With more than one backend the vhost gets an nginx `upstream` (`least_conn`): a backend that
fails twice is skipped for 10 s, and a request that hits a restarting or dead backend is retried
on the next one. Spreading requests means the app must not keep state in memory or on local disk
(sessions, uploads, caches): use the database or Supabase storage. Static and PHP sites are
served by nginx directly and need none of this.

### While a site deploys

On a first deployment the domain answers immediately with a **“Deploying…” page** (nginx placeholder
vhost) and gets its certificate **before** the build, so visitors never land on another site's
vhost (the `421 Misdirected Request` you would otherwise get over HTTP/2). The real site replaces
it once the health check passes; if the first deployment fails, a “not available yet” page stays.
Proxied apps also show the page during the second or two of a restart instead of a 502.

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
- **Authentication**: administrator accounts are created on the server (`mas user create`), never
  from the web. CMS editor accounts are created by an administrator in the dashboard and can only
  reach the CMS routes of the sites they were given. Passwords are hashed with argon2id. **TOTP 2FA** (RFC 6238, 30 s, ±1 step), and
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
- **Hardening from the security review**: build/start commands are single-line (no systemd
  directive injection), the served folder cannot leave the release (`..` refused), GitHub paths
  and refs are validated and URL-encoded, each webhook delivery is accepted once, replacing the 2FA
  device requires a current code, password checks inside a session are rate-limited, the session
  cookie is `__Host-` prefixed, `Permissions-Policy` and `Cross-Origin-Opener-Policy` headers are
  set, and app units run with no capabilities (`CapabilityBoundingSet=`), `PrivateDevices`,
  `ProtectKernel*`, `RestrictSUIDSGID`.
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

## Beta: known limitations

- Debian (and derivatives) with nginx only; Apache/Caddy servers are not detected.
- One admin account; no 2FA recovery codes yet (`sudo mas user 2fa-reset` on the server).
- No pull-request preview deployments yet; one production branch per app.
- DNS providers other than OVH and Cloudflare are implemented from their API documentation and
  not yet battle-tested: please report issues.
- The dashboard CSP keeps `'unsafe-inline'` (required by the static Next.js export).

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
- [x] **Load balancing on one server**: several instances per app, outside backends, rolling restarts.
- [ ] **Cluster**: choose the role at install time (`standalone`, `primary`, `node`); a primary
      deploys an app on several nodes through a Mas agent and balances between them.
- [ ] Later: preview deployments per pull request, recovery codes for 2FA, notifications.

## License

MIT
