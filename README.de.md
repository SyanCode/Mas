<div align="center">

[English](README.md) · [Français](README.fr.md) · [Español](README.es.md) · **Deutsch**

# Mas

**Dein Server, erfasst und deployt.**
Selbst gehostetes Dashboard, das übernimmt, was bereits auf deinem Debian + nginx-Server läuft, bei jedem Push von GitHub deployt,
fertige Dienste betreibt und deine Apps auf mehrere Server verteilt.

![Status: Beta](https://img.shields.io/badge/status-beta-8b5cf6)
![Lizenz: MIT](https://img.shields.io/badge/license-MIT-0ea5e9)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-22c55e)
![Debian + nginx](https://img.shields.io/badge/Debian%20%2B%20nginx-supported-f59e0b)

![Mas-Dashboard: eine App mit ihrem Produktions-Deployment und dem Live-Build-Log](docs/app.jpg)

</div>

**Mas erfasst den Server, den du schon hast.** Richte es auf einen Debian + nginx-Server, der seit
Jahren läuft, mit einer Mischung aus PHP-Websites, Node-Apps unter PM2, ein paar systemd-Units und ein
oder zwei Docker-Containern, und es zeigt dir, was läuft, wo, für welche Domain, unter
welchem Benutzer und aus welchem Git-Repository. Nichts wird neu installiert oder kaputt gemacht.

Danach verwaltest du diese Dienste über die `mas`-CLI oder über ein Web-Dashboard, das durch ein
Passwort und Zwei-Faktor-Authentifizierung geschützt ist, behältst Zertifikate und nginx im Blick, verknüpfst Domains
deines Registrars (OVH, Cloudflare, Gandi, Hetzner, DigitalOcean, Porkbun) und deployst
Projekte bei jedem Push von GitHub, wie bei Vercel, auf derselben Maschine. Wenn ein Server nicht
reicht, treten weitere Server als Agents bei und betreiben Kopien deiner Apps hinter demselben Load Balancer.

## Auf einen Blick

| | |
|---|---|
| 🔎 **Erkennung** | nginx-vhosts, Ports, `/proc`, systemd, PM2 und Docker werden zu einer einzigen Liste von Diensten zusammengeführt: Domain, Benutzer, Ordner, Git-Repository. Nur lesend, ohne KI. |
| 🚀 **Deployen von GitHub** | Atomare Releases bei jedem Push, generierte systemd-Unit und nginx-vhost, Let's Encrypt, Health-Check, automatisches Rollback, Live-Build-Logs. |
| 🌐 **Domains** | DNS-Einträge werden bei OVH, Cloudflare, Gandi, Hetzner, DigitalOcean oder Porkbun angelegt, dann HTTPS. |
| 🗄️ **Supabase** | Der offizielle Stack mit einem Klick selbst gehostet, Apps direkt aus ihrem Repository verbunden, Migrationen beim Deploy. |
| 📦 **Fertige Dienste** | Mailserver, Minecraft, Uptime Kuma, Vaultwarden, n8n, Gitea oder ein beliebiges Docker-Image, mit nächtlichen Backups. |
| ✏️ **CMS** | Deine Kunden bearbeiten Texte, Bilder und Artikel ihrer Website; jedes Speichern ist ein Commit und ein Deploy. |
| 📊 **Traffic** | Anfragen, Seitenaufrufe und Besucher pro Tag für jede Website, aus den nginx-Logs, ohne Cookies. |
| 🧠 **Ressourcen** | Arbeitsspeicher und CPU von allem auf einer Seite; Speicherlimits, Schlafen bei Inaktivität, Instanzen, die dem Traffic folgen, Ratschläge mit einem Klick angewendet. |
| 🛠️ **Betrieb** | Health-Checks mit Lösungen, jeden Dienst identisch neu starten, Wartungsseiten, alles neu deployen. |
| 🖧 **Cluster** | Agents ohne Dashboard, Kopien von Apps auf mehreren Servern, Live-Karte, automatisches Failover, Beförderung. |

## Inhalt

- [Warum noch eins?](#warum-noch-eins) · [Installation](#installation) · [CLI](#cli) · [Dashboard](#dashboard)
- [Deployen von GitHub](#deployen-von-github) · [Domains](#domains-ovh-cloudflare-gandi-hetzner-digitalocean-porkbun) ·
  [Supabase](#selbst-gehostetes-supabase) · [Fertige Dienste](#fertige-dienste) · [CMS](#cms-für-deine-kunden)
- [Den Server betreiben](#den-server-betreiben): [Ressourcen](#ressourcen), [Traffic](#traffic-statistiken),
  [Wartung](#wartungsmodus), [alles neu starten](#alles-neu-starten-identisch), [Health-Checks](#health-checks)
- [Mehrere Server](#mehrere-server-cluster) · [So funktioniert die Erkennung](#so-funktioniert-die-erkennung) ·
  [Sicherheitsmodell](#sicherheitsmodell) · [Entwicklung](#entwicklung) · [Roadmap](#roadmap)

> [!WARNING]
> **Beta.** Mas läuft als root und bearbeitet nginx, systemd und DNS. Jede Änderung wird gesichert und
> validiert (`nginx -t`), und die Erkennung ist nur lesend, aber probiere es zuerst auf einem Server aus,
> den du wiederherstellen kannst, und lies das [Sicherheitsmodell](#sicherheitsmodell). Feedback und Issues sind sehr willkommen.

## Warum noch eins?

Coolify, Dokploy und CapRover sind großartig, aber sie wollen einen sauberen Server: Alles muss
in ihren Containern laufen, hinter ihrem Proxy. Wenn dein Server bereits zehn von Hand
konfigurierte Websites hostet, heißt eines davon einzuführen, alles zu migrieren.

Mas fängt am anderen Ende an:

|                         | Coolify / Dokploy / CapRover | Mas                                   |
|-------------------------|------------------------------|---------------------------------------|
| Ausgangspunkt           | Frischer Server              | Dein bestehender Server               |
| Reverse Proxy           | Traefik / Caddy (ihrer)      | Dein nginx, deine vhosts              |
| Laufzeitumgebungen      | Nur Docker                   | PHP-FPM, systemd, PM2, Docker, statisch |
| Bestehende Apps         | Neu deployen                 | Erkannt und an Ort und Stelle übernommen |
| Erkennung               | n/a                          | Deterministisches Parsen, ohne KI     |
| Inaktive Apps           | Laufen immer                 | Können schlafen und beim nächsten Besuch aufwachen |

Jede Erkennung ist deterministisch: Parsen von Konfigurationsdateien, `/proc`, `ss`, `systemctl`, `pm2 jlist`
und der Docker-Socket. Nirgendwo ist ein KI-Modell beteiligt, und die Erkennung braucht keine externe API
(nur die optionalen Integrationen für GitHub und DNS-Anbieter sprechen mit deren eigenen APIs).

## Installation

Auf dem Server (Debian 12+, als root):

```console
$ git clone https://github.com/SyanCode/Mas.git /opt/mas-install
$ cd /opt/mas-install
$ sudo ./deploy/install.sh --domain mas.example.com --email you@example.com
```

Ohne `--domain` bleibt das Dashboard privat (SSH-Tunnel, siehe unten). Updates:
`sudo mas self-update` (git pull, Neuinstallation, Neubau, Neustart).

Der Installer ist idempotent und fragt nach zwei Dingen:

1. **Das Admin-Passwort, dann 2FA**: Im Terminal wird ein QR-Code ausgegeben; scanne ihn mit
   Google Authenticator, Aegis, 1Password, Bitwarden… und gib den 6-stelligen Code ein.
2. **Die Domain des Dashboards** (z. B. `mas.example.com`, ihr DNS muss auf den Server zeigen). Mas holt
   ein Let's-Encrypt-Zertifikat und installiert einen HTTPS-vhost. Lass sie leer, um das Dashboard
   privat zu halten und es über einen SSH-Tunnel zu erreichen:
   `ssh -L 7070:127.0.0.1:7070 you@server`, dann öffne http://localhost:7070.

Nicht interaktiv: `sudo ./deploy/install.sh --domain mas.example.com --email you@example.com`.

Was installiert wird: `/opt/mas/src` (Quellcode), `/opt/mas/venv`, `/etc/mas/config.toml`,
`/var/lib/mas/mas.db`, `mas.service`, `/etc/nginx/sites-available/mas.conf` und `/usr/local/bin/mas`.

### Aktualisieren

```console
$ sudo mas self-update                       # follow the published branch (git checkout)
$ sudo mas self-update --from mas.tar.gz     # from a `git archive` tarball
```

Es aktualisiert den Quellcode, installiert neu, baut das Dashboard neu und startet `mas.service` neu. Ein Git-Checkout
wird auf `origin/<branch>` gesetzt, auch wenn dieser Branch per Force-Push überschrieben wurde; einen Checkout
mit lokalen Änderungen rührt es nicht an. Eine Installation aus einem Archiv behält den vorherigen Quellcode als
`/opt/mas/src.mas-bak-<date>`.

Wenn eine neuere Version veröffentlicht ist, beginnt jeder `sudo mas …`-Befehl mit einem einzeiligen Hinweis,
`sudo mas self-update` auszuführen (höchstens alle 6 Stunden gegen GitHub geprüft, 2 s Timeout, still, wenn
offline; `MAS_NO_UPDATE_CHECK=1` schaltet es ab).

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

`<service>` ist die von `mas scan` angezeigte ID (meist die Domain) oder ein beliebiger eindeutiger Teil davon.

| Manager        | Aktionen                       | Befehl                                            |
|----------------|--------------------------------|---------------------------------------------------|
| `systemd`      | start, stop, restart, reload   | `systemctl <action> <unit>`                       |
| `pm2`          | start, stop, restart, reload   | `runuser -u <owner> -- pm2 <action> <app>`        |
| `docker`       | start, stop, restart           | Docker-API `POST /containers/<id>/<action>`       |
| `php-fpm`      | reload, restart                | `systemctl <action> php*-fpm.service`             |
| `nginx-static` | reload                         | `nginx -t && systemctl reload nginx`              |

## Dashboard

![Übersicht: jeder auf dem Server erkannte Dienst](docs/overview.jpg)

Drücke überall <kbd>⌘K</kbd> / <kbd>Ctrl</kbd>+<kbd>K</kbd>, um zu einem Dienst, einer App oder einer Seite zu springen.

Das Dashboard spricht **Englisch, Französisch, Spanisch und Deutsch**: Standardmäßig wird die Sprache des
Browsers verwendet, und die Auswahl im Benutzermenü (und auf der Anmeldeseite) merkt sich deine Wahl. Der
englische Text ist der Schlüssel (`t("Deploy Supabase")`), die Übersetzungen liegen in `web/locales/<lang>.json`,
und `tests/test_i18n.py` schlägt fehl, wenn ein Text keine Übersetzung hat. Eine Sprache hinzuzufügen heißt: eine JSON-Datei
plus eine Zeile in `web/lib/i18n.tsx`.

| Seite | Was sie zeigt |
|---|---|
| **Übersicht** | jeden erkannten Dienst: Status, Domains, Typ, Manager, Port, SSL-Ablauf (orange unter 14 Tagen), GitHub-Repository, die Mas-App, zu der er gehört; Filter und Suche; **Alles neu starten**, **nginx neu laden** |
| **Dienst** | Details, Live-Arbeitsspeicher / CPU / Laufzeit / Ports und Links, Logs, Aktionen (mit Bestätigung), Traffic seiner Website, Verlauf, Anzeigename und Notizen, **Durch eine Mas-App ersetzen** |
| **Apps** | von GitHub deployte Apps und Dienste aus dem Katalog; **Alles neu deployen**, **Wartung** |
| **App** | Deployments mit Live-Build-Logs, Rollback, Domain, Traffic, Ressourcen, Lastverteilung, Wartung, Datenbank, CMS, Umgebungsvariablen |
| **Datenbanken** | das selbst gehostete Supabase: Bestandteile, Arbeitsspeicher pro Container, Zugriff auf Studio, Backups |
| **Ressourcen** | Arbeitsspeicher und CPU von allem auf dem Server, Limits, Schlafen, Instanzen, Ratschläge |
| **Server** | der Cluster: dieser Server, die anderen, Beitritts-Tokens, Live-Karte der Anfragen |
| **Zustand** | Prüfungen von Zertifikaten, certbot, nginx und DNS, mit den Lösungen |
| **Aktivität** | das Audit-Log: wer was wann mit welchem Ergebnis getan hat |
| **Einstellungen** | 2FA, Passwort, GitHub App, DNS-Anbieter, Personen (CMS-Redakteure und Einladungen) |

Helles und dunkles Theme (folgt standardmäßig dem System).

## Deployen von GitHub

### 1. GitHub verbinden (einmalig)

Klicke unter **Einstellungen → GitHub** auf **GitHub App erstellen** (optional für eine Organisation). GitHub
zeigt die zu erstellende App an; bestätige, und GitHub schickt ihre Zugangsdaten über den
[Manifest-Flow](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest) an Mas zurück:
App-ID, privater Schlüssel (gespeichert in `/etc/mas/github-app.pem`, `0600`) und Webhook-Secret (gespeichert in
`/etc/mas/config.toml`, `0600`). Klicke dann auf **Install** und wähle die Repositories, die Mas lesen darf.

Die App ist privat und verlangt: **Contents: read and write** (Schreiben wird nur vom CMS genutzt, um
Inhaltsänderungen zu committen), **Metadata: read**, **Pull requests: read** sowie die Webhook-Events **push** und
**pull_request**. Eine App, die vor dem CMS erstellt wurde, hat Contents nur lesend: Ändere das
in den App-Einstellungen auf GitHub auf *Read and write* und akzeptiere dann die neuen Berechtigungen in der
Installation.

<details>
<summary>Die App stattdessen von Hand erstellen</summary>

1. github.com → Settings → Developer settings → GitHub Apps → **New GitHub App**.
2. Homepage URL: `https://<your dashboard>`; Webhook URL: `https://<your dashboard>/api/github/webhook`;
   Webhook secret: eine lange zufällige Zeichenkette.
3. Berechtigungen: Contents read and write, Metadata read-only, Pull requests read-only. Events: Push, Pull request.
4. „Only on this account“, erstellen, dann **Generate a private key** (lädt eine `.pem` herunter).
5. Auf dem Server: `sudo mas github setup` (fragt nach App-ID, Slug, `.pem`-Pfad und Secret), dann
   `sudo systemctl restart mas`, und installiere die App in deinen Repositories.
</details>

### 2. Eine App erstellen

**Apps → Neue App**: Wähle ein Repository und einen Branch. Mas erkennt den Typ (Next.js, Node, Python,
statisch, PHP) aus dem Repository; lege optional eine Domain und Umgebungsvariablen fest (verschlüsselt
gespeichert mit einem Schlüssel in `/etc/mas/secret.key`). Erstellen: Das erste Deployment startet, und sein Log
läuft live im Dashboard mit.

Jeder Push auf den Branch wird dann automatisch deployt:

1. `git clone` des gepushten Commits nach `/srv/mas/apps/<app>/releases/<sha>/` (das Installations-Token
   wird über die Umgebung an git übergeben, nie auf der Kommandozeile oder in Logs)
2. Build als dedizierter Benutzer `mas-<app>` (`npm ci && npm run build`, ein Python-venv +
   `pip install`, `composer install` oder dein eigener Build-Befehl)
3. atomares Umschalten des Symlinks `current` (eine Next.js-App, die mit `output: 'export'` gebaut wurde, hat keinen
   Server: Mas liefert ihren Ordner `out/` mit nginx aus und lässt die systemd-Unit weg)
4. `mas-app-<app>.service` (generierte systemd-Unit, Benutzer ohne root, `NoNewPrivileges`,
   `ProtectSystem`, `ProtectHome`, App lauscht auf `127.0.0.1:<port>`) wird neu gestartet
5. nginx-vhost aus einer Vorlage generiert (Backup, `nginx -t`, Reload; abgelehnt, wenn ein anderer vhost
   die Domain bereits ausliefert)
6. `certbot certonly --nginx`, falls die Domain noch kein Zertifikat hat, dann HTTPS-vhost
7. HTTP-Health-Check; **schlägt er fehl, wird das vorherige Release automatisch wiederhergestellt**

Die letzten 5 Releases werden behalten; **Zurücksetzen** stellt jedes davon mit einem Klick wieder her (`mas rollback <app>`).

### Domains (OVH, Cloudflare, Gandi, Hetzner, DigitalOcean, Porkbun)

Mas kann die DNS-Einträge selbst anlegen. Unter **Einstellungen → Domains**:

1. Trage die **öffentliche IP** dieses Servers ein (Mas schlägt die Adresse vor, auf die deine bestehenden Domains zeigen).
2. Verbinde einen oder mehrere Registrare. Jede Karte verlinkt auf die Token-Seite des Anbieters und listet die
   genauen Rechte auf, die du vergeben musst (Zonen lesen, Einträge anlegen/aktualisieren; Mas löscht nie einen Eintrag). Die Schlüssel
   werden beim Anbieter geprüft und dann verschlüsselt gespeichert.

| Anbieter | Zugangsdaten |
|----------|-------------|
| OVHcloud | Application Key + Secret + Consumer Key (Token-Seite mit vorausgefüllten Rechten) |
| Cloudflare | API-Token, Vorlage „Edit zone DNS“ (Einträge werden als DNS-only angelegt, graue Wolke) |
| Gandi | Personal Access Token (LiveDNS) |
| Hetzner DNS | API-Token |
| DigitalOcean | API-Token mit dem Scope `domain` |
| Porkbun | API-Key + Secret (API-Zugriff für jede Domain aktivieren) |

Namecheap wird nicht unterstützt: Seine API braucht eine freigeschaltete IP und schreibt bei jeder Änderung die ganze Zone neu.

Wähle dann bei einer App (oder in *Neue App*) eine deiner Domains und eine Subdomain (standardmäßig der
App-Name). Mas prüft den Namen live (frei, zeigt bereits hierher, zeigt woandershin, CNAME,
bereits verwendet), legt den `A`- (und `AAAA`-)Eintrag an, wartet, bis er aufgelöst wird, und deployt dann mit der
nginx-Website und dem HTTPS-Zertifikat. Ein Eintrag, der woandershin zeigt, wird nur nach ausdrücklicher
Bestätigung ersetzt; ein CNAME wird nie angefasst.

CLI: `mas dns connect <provider>`, `mas dns ip <ipv4> [--ipv6 …]`, `mas dns zones`,
`mas domain <app> <sub.example.com> [--overwrite | --no-dns]`.

### Selbst gehostetes Supabase

**Datenbanken → Supabase deployen** installiert den offiziellen Supabase-Docker-Stack (Postgres, Auth,
REST, Realtime, Storage, Studio) auf dem Server:

- jeder veröffentlichte Port wird an `127.0.0.1` gebunden (Docker würde sonst die Firewall umgehen);
- nur die öffentliche API (`/rest`, `/auth`, `/realtime`, `/storage`, `/functions`, `/graphql`) wird
  über nginx veröffentlicht, auf der Domain deiner Wahl (DNS-Eintrag wie bei Apps angelegt), mit HTTPS;
- **Studio bleibt privat**: Auf der Supabase-Domain antwortet es nur dem lokalen Netzwerk und den
  Adressen, die du erlaubst (Seite Datenbanken), hinter seinem Login; ansonsten über den SSH-Tunnel, den Mas anzeigt;
- Secrets (Postgres-Passwort, JWT-Secret, Schlüssel `anon` / `service_role`) werden von Mas generiert und
  verschlüsselt gespeichert;
- jeden Tag läuft ein `pg_dump`, die letzten 7 werden in `/var/lib/mas/backups/supabase` behalten.

Eine Instanz pro Server (alle verbundenen Apps teilen sich ihre Datenbank); der Stack braucht Docker mit
dem Compose-Plugin.

**Was installieren?** Wähle, was deine Apps brauchen; Mas schlägt die Wahl vor, die zum freien Arbeitsspeicher passt:

| Wahl | Was Apps können | ≈ RAM |
|---|---|---|
| **Basis** | Datenbank, Benutzerkonten, API | 400 MB |
| **Empfohlen** | + Datei-Uploads, Live-Aktualisierungen, Admin-Oberfläche | 880 MB |
| **Komplett** | + Bildskalierung, Serverfunktionen, Logs, viele Verbindungen | 1.8 GB |

**Anpassen** zeigt die Bestandteile einzeln, und die Seite Datenbanken ändert sie später
(`docker compose up -d --remove-orphans`, Daten-Volumes bleiben erhalten):

| Bestandteil | Name bei Supabase | Empfohlen | ≈ RAM |
|---|---|---|---|
| Datenbank, Benutzerkonten und API (immer an) | Postgres, Auth, REST, Kong | an | 400 MB |
| Admin-Oberfläche (privat, Start/Stopp bei Bedarf) | Studio + pg-meta | an | 250 MB |
| Live-Aktualisierungen | Realtime | an | 150 MB |
| Datei-Uploads | Storage | an | 80 MB |
| Bildskalierung | imgproxy | aus | 60 MB |
| Serverfunktionen | Edge Functions | aus | 150 MB |
| Logs und Statistiken | Logflare + Vector | aus | 600 MB |
| Viele Verbindungen | Supavisor (aus: Postgres wird direkt auf `127.0.0.1` veröffentlicht) | aus | 150 MB |

#### Eine App verbinden

Die Karte **Datenbank** einer App liest ihr Repository und passt sich daran an:

- **Sie erkennt, dass die App Supabase nutzt**, an ihren Bibliotheken (`@supabase/supabase-js`,
  `@supabase/ssr`, `supabase` für Python…), ihrer Beispiel-env-Datei oder einem Ordner `supabase/`. Wenn der
  Server noch kein Supabase hat, installiert **Datenbank anlegen und verbinden** es und verbindet die App,
  sobald es läuft. In **Apps → Neue App** wird eine Supabase-App vor ihrem ersten Build verbunden.
- **Datenbank verbinden** schreibt die URL und die Schlüssel unter die Namen, die der Code liest: die üblichen
  (`SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
  `DATABASE_URL`…), die des Frameworks (`VITE_…`, `PUBLIC_…` für Astro und SvelteKit, `EXPO_PUBLIC_…`)
  und jede Supabase-Variable ihrer `.env.example`. Ein Secret (service_role-Schlüssel, Postgres-URL) wird
  nie in eine Variable geschrieben, die der Browser lesen kann (`NEXT_PUBLIC_`, `VITE_`…): Mas listet es stattdessen auf.
- **Das Repository beschreibt seine Datenbank** mit der Struktur der Supabase-CLI:
  `supabase/migrations/<timestamp>_<name>.sql` und `supabase/seed.sql`. Bei jedem Deploy einer
  verbundenen App führt Mas die neuen Migrationen vor dem Build aus, der Reihe nach, jede in einer Transaktion,
  und vermerkt sie in `supabase_migrations.schema_migrations` wie `supabase db push`. Schlägt eine
  fehl, bricht der Deploy ab und die Website bleibt auf ihrer aktuellen Version. Der Seed wird einmal geladen,
  wenn du ihn beim Verbinden ankreuzt. Die Karte listet die Migrationen auf, angewendet oder ausstehend.
- **Meine Entwicklungsdatenbank importieren** kopiert das Schema `public` eines supabase.com-Projekts (Tabellen,
  Funktionen, Policies, mit oder ohne Zeilen) und verbindet die App. Füge den Connection-String des *Session pooler*
  ein (Projekt → **Connect**); er wird `pg_dump` über die Umgebung übergeben, nie
  auf einer Kommandozeile, und nicht aufbewahrt. Tabellen, die hier bereits existieren, werden abgelehnt, außer du kreuzt
  *ersetzen* an. Mas führt ein `pg_dump` aus, das so aktuell ist wie der Quellserver. Nicht kopiert werden: Benutzer
  (`auth`), Dateien (Storage) und Trigger auf `auth.users`.

Eine App, die mit einem anderen Supabase-Projekt verbunden ist (ihre `SUPABASE_URL` zeigt woandershin), wird in Ruhe gelassen.

Die verbleibenden Dienste sind für einen kleinen Server abgestimmt: Postgres `shared_buffers=64MB` und weniger
Work Memory, ein Kong-Worker mit 32 MB Cache, ein begrenzter Node.js-Heap für Studio, Storage und
pg-meta. Die unveränderte Upstream-Datei wird als `docker-compose.original.yml` behalten; die Seite zeigt
den tatsächlich von jedem Container genutzten Arbeitsspeicher.

Ein Backup wiederherstellen: `docker cp <file> supabase-db:/tmp/r.dump && docker exec supabase-db pg_restore
-U postgres -d postgres --clean /tmp/r.dump`.

### Fertige Dienste

**Apps → Dienst bereitstellen** betreibt fertige Server mit Docker, ganz ohne Repository: Wähle einen aus, fülle
ein kurzes Formular aus, Mas schreibt `/srv/mas/stacks/<name>/docker-compose.yml`, öffnet die Ports, holt das
Zertifikat für seine Adresse und zeigt, wie man ihn nutzt.

| Dienst | Was du bekommst |
|---|---|
| **Minecraft-Server (Java)** | Paper, Vanilla, Fabric, Forge oder Purpur; Version, Arbeitsspeicher, Spielmodus, erlaubte Spieler, Operatoren |
| **Minecraft-Server (Bedrock)** | für Smartphones, Konsolen und Windows (UDP 19132) |
| **Mailserver** | docker-mailserver: deine `@yourdomain`-Postfächer in jeder Mail-App (IMAP 993, SMTP 465/587), DKIM-Schlüssel angelegt, die hinzuzufügenden DNS-Einträge (A, MX, SPF, DKIM, DMARC) mit ihren Werten aufgelistet, **Postfach hinzufügen** mit einem Klick |
| **Uptime Kuma**, **Vaultwarden**, **n8n**, **Gitea** | auf ihrer eigenen Domain mit HTTPS ausgeliefert |
| **Beliebiges Docker-Image** | ein Image, sein Web-Port auf einer Domain und/oder unverändert veröffentlichte Ports, seine Umgebung und ein dauerhafter Ordner |

- Webseiten lauschen auf `127.0.0.1`, und nginx liefert sie mit HTTPS aus. Für außen gedachte Ports
  (25565, 25, 993…) werden von Docker auf allen Interfaces veröffentlicht, was sie in der Firewall öffnet;
  Mas lehnt einen Port ab, den bereits ein anderer Dienst nutzt.
- Passwörter (erstes Postfach, Admin-Token von Vaultwarden) werden generiert und verschlüsselt aufbewahrt; die
  Seite des Dienstes zeigt sie auf Wunsch an, zusammen mit der Adresse, mit der du dich verbindest.
- Die Seite des Dienstes hat **Starten / Stoppen / Neu starten**, seine Logs und seine Einstellungen: Speichern
  deployt ihn neu. **Neu deployen** holt außerdem das neueste Image. Die Daten bleiben über Updates hinweg in `/srv/mas/stacks/<name>/data`;
  beim Entfernen des Dienstes bleiben sie erhalten, außer du kreuzt *Auch seine Daten löschen* an.
- **Backups**: Jede Nacht zwischen 3 und 5 Uhr (Serverzeit) stoppt Mas den Dienst, kopiert seinen
  Ordner `data` nach `/var/lib/mas/backups/stacks/<name>/<name>-<date>.tar.gz` und startet ihn wieder
  (eine Minecraft-Welt oder ein Postfach, das während des Schreibens kopiert wird, ließe sich nicht wiederherstellen). Die letzten 7 werden behalten
  (`stacks_keep_backups` in `[deploy]`), und sie überdauern den Dienst. **Jetzt sichern** und
  **Wiederherstellen** befinden sich auf der Seite des Dienstes; eine Wiederherstellung legt die aktuellen Daten zuerst in
  `data.before-restore` beiseite.
- Minecraft-Server starten mit einem Heap von 512 MB, der bis zum gewählten Arbeitsspeicher wächst, und geben ihn zurück,
  wenn niemand spielt.
- Für Mail: Bitte deinen Hosting-Anbieter um den Reverse-DNS-Eintrag (PTR) des Servers und prüfe, dass er
  den ausgehenden Port 25 nicht blockiert.

Neue Dienste sind eine Vorlage in `mas/stacks.py` (eine aus dem Formular gebaute Compose-Datei); führe nach einer Änderung
des Katalogs `python -m mas.stacks > web/lib/stackTexts.ts` aus, damit seine Texte übersetzt werden.

### CMS für deine Kunden

Hoste Kunden-Websites mit Mas und lass jeden Kunden seine eigenen Inhalte bearbeiten (Texte, Bilder, Artikel),
ohne ihm Zugriff auf den Server oder den Code zu geben. Die Website behält ihr Design: Das Repository
legt in **`mas.cms.json`** fest, was bearbeitet werden darf, der Kunde füllt Formulare im Dashboard aus, und Mas
schreibt die Werte, als Commit auf den deployten Branch (was die Website neu deployt) oder, bei Sammlungen,
die in Supabase gespeichert sind, als Tabellenzeilen, die sofort online sind.

**→ Die vollständige Referenz ist [docs/cms.md](docs/cms.md)**: Seiten, Sammlungen (Artikel, News…),
Feldtypen, Speicherung in git oder Supabase, wie die Website ihre Inhalte liest, und eine Checkliste.

```json
{
  "$schema": "https://raw.githubusercontent.com/SyanCode/Mas/main/schema/mas.cms.schema.json",
  "pages": [{ "id": "home", "label": "Home page", "file": "content/home.json",
              "fields": [{ "key": "title", "type": "text", "max": 80, "required": true },
                         { "key": "hero", "type": "image" }] }],
  "collections": [{ "id": "articles", "label": "Articles", "dir": "content/articles",
                    "fields": [{ "key": "title" }, { "key": "date", "type": "date" },
                               { "key": "body", "type": "markdown" }] }]
}
```

**Eine Website Mas-kompatibel machen.** Installiere mit Claude Code einmalig das Plugin dieses Repositorys
und bitte dann im Projekt der Website in einfachen Worten darum:

```
/plugin marketplace add SyanCode/Mas
/plugin install mas-cms@mas

Rends ce site compatible Mas
```

Claude verschiebt die bearbeitbaren Inhalte in JSON-Dateien, schreibt `mas.cms.json`, bindet die Website daran an,
ohne ihr Design anzufassen, und prüft das Ergebnis mit dem `check.py` des Plugins. Von Hand: Folge der
[Checkliste](docs/cms.md#making-a-site-compatible-checklist) und beginne `mas.cms.json` mit der
`$schema`-Zeile oben, um Autovervollständigung in deinem Editor zu bekommen. Die Karte **CMS** einer App kann
`mas.cms.json` auch aus den JSON-Dateien schreiben, die bereits im Repository liegen.

In **Apps → Neue App** zeigt ein Repository mit gültiger `mas.cms.json` ein Badge **Mas-kompatibel** (oder
*ungültige mas.cms.json* mit dem Grund beim Darüberfahren). Dann:

1. Gib deinem Kunden ein Konto, unter **Einstellungen → Personen**, auf eine von drei Arten:
   - **Einladungslink** (am einfachsten): Wähle die Websites aus, schick den Link. Er funktioniert einmal, 7 Tage lang,
     auch wenn die Registrierung geschlossen ist; der Kunde wählt einen Benutzernamen und ein Passwort und landet auf
     seiner Website. Gespeichert wird nur ein Hash des Links, und ausstehende Links lassen sich widerrufen.
   - **Registrierung öffnen**: Jeder kann unter `/signup/` ein Konto anlegen, optional nur mit einem
     Einladungscode, den du festlegst. Ein neues Konto sieht nichts, bis du ihm eine Website gibst. Registrierungen sind
     auf 5 pro Stunde und Adresse begrenzt und werden ins Audit-Log geschrieben.
   - **Neuer Redakteur**: Du wählst den Benutzernamen, Mas generiert ein Passwort, das einmal angezeigt wird.

   **Websites** bei jedem Redakteur legt fest, welche Apps er bearbeiten darf; die Seite der App hat denselben Schalter in
   ihrer Karte **CMS**, die außerdem das Schema prüft (und bei Supabase-Sammlungen die Tabelle anlegt).
2. Der Kunde meldet sich am selben Dashboard an und sieht nur **Meine Websites** und **Konto** (Passwort,
   2FA). Jede andere API-Route antwortet einem Redakteur mit `403`.

Was die Website schützt: Das Schema wird immer aus dem Repository gelesen (nie aus dem Browser);
nur die deklarierten Felder der deklarierten Dateien (oder Tabellen) werden geschrieben, und Schlüssel, die das Schema nicht
kennt, bleiben unangetastet; Werte werden serverseitig geprüft (Länge, Typ, erlaubte Auswahlen, Links
beschränkt auf `https://`, `/`, `#`, `mailto:` und `tel:`, keine Steuerzeichen); `.git/`, `.github/`
und `mas.cms.json` selbst können nicht als Ziel dienen; Bilder werden anhand ihres Inhalts geprüft (PNG, JPEG, GIF,
WebP, AVIF; kein SVG) und bekommen einen neuen Dateinamen. Jedes Speichern ist ein einzelner Commit (`cms(home): … Edited
by <editor>`) auf der Version, die der Redakteur geöffnet hat: Hat sie in der Zwischenzeit jemand geändert, wird der Redakteur
gebeten, neu zu laden, statt zu überschreiben. Mach eine Änderung mit `git revert` rückgängig oder setze das
Deployment in Mas zurück. Supabase-Sammlungen werden mit dem service_role-Schlüssel geschrieben, der Mas nie
verlässt; die Website liest sie mit ihrem anon-Schlüssel (Row Level Security: nur lesen).

### Lastverteilung

Die Karte **Lastverteilung** einer Node.js-, Next.js- oder Python-App verteilt ihren Traffic:

- **Instanzen auf diesem Server**: Mas startet die App N-mal (`mas-app-<id>.service`,
  `mas-app-<id>.2.service`…, jede auf ihrem eigenen Loopback-Port), um jeden CPU-Kern zu nutzen. Ein Neu-Deploy
  startet sie nacheinander neu und prüft jede, bevor die nächste drankommt, sodass die Website nie ausfällt;
  kommt eine Instanz nicht wieder hoch, wird das Release zurückgesetzt.
- **Server des Clusters**: von Mas gebaute Kopien auf den Agents, die du ankreuzt (siehe
  [Mehrere Server](#mehrere-server-cluster)).
- **Weitere Server**: `host:port` von Kopien der App, die du anderswo betreibst, typischerweise über ein
  privates Netzwerk. Sie werden demselben Pool hinzugefügt.

Mit mehr als einem Backend bekommt der vhost ein nginx-`upstream` (`least_conn`): Ein Backend, das
zweimal fehlschlägt, wird 10 s lang übersprungen, und eine Anfrage, die auf ein neu startendes oder totes Backend trifft, wird
beim nächsten wiederholt. Anfragen zu verteilen heißt, dass die App keinen Zustand im Arbeitsspeicher oder auf der lokalen Festplatte halten darf
(Sessions, Uploads, Caches): Nutze die Datenbank oder Supabase Storage. Statische und PHP-Websites werden
direkt von nginx ausgeliefert und brauchen nichts davon.

### Während eine Website deployt wird

Bei einem ersten Deployment antwortet die Domain sofort mit einer **Seite „Wird deployt…“** (nginx-Platzhalter-vhost)
und bekommt ihr Zertifikat **vor** dem Build, sodass Besucher nie auf dem vhost einer anderen Website landen
(der `421 Misdirected Request`, den du sonst über HTTP/2 bekämst). Die echte Website ersetzt
sie, sobald der Health-Check besteht; schlägt das erste Deployment fehl, bleibt eine Seite „noch nicht verfügbar“ stehen.
Apps hinter dem Proxy zeigen die Seite auch während der ein, zwei Sekunden eines Neustarts statt eines 502.

### Eine erkannte Website durch eine App ersetzen

Eine von Mas erkannte Website (ein PHP-Ordner, eine statische Website, eine PM2-App…) kann durch eine Mas-App ersetzt werden, die aus
ihrem Repository gebaut wird, **ohne Unterbrechung**: Die App wird neben der alten Website gebaut und geprüft, dann
verschwinden die alten nginx-Dateien, und der vhost der App kommt im selben Reload hinzu. Schlägt das erste Deployment
fehl, bleibt die alte Website, wie sie war. Die Seite des Dienstes bietet **Durch eine Mas-App ersetzen** an; wenn du
die App später entfernst, kommt die alte Website zurück.

### Alles neu deployen

**Apps → Alles neu deployen** reiht ein Deployment des neuesten Commits jeder App von GitHub in die Warteschlange ein; sie laufen
nacheinander (Dienste aus dem Katalog werden nicht angefasst).

### Einen bestehenden Dienst übernehmen

Ein erkannter Dienst, dessen Ordner ein GitHub-Checkout ist (zum Beispiel eine PM2- oder systemd-App), zeigt
auf seiner Seite **Mit GitHub verbinden**. Mas deployt ihn dann **an Ort und Stelle**, ohne etwas neu zu installieren:
`git fetch` + `git reset --hard` in seinem Ordner (als Besitzer des Ordners, abgelehnt bei lokalen
nicht committeten Änderungen), Build, Neustart über seinen aktuellen Manager, Health-Check und
`git reset` zurück auf den vorherigen Commit, falls er fehlschlägt.

### CLI

```console
$ sudo mas apps                    # apps, live release, last deploy
$ sudo mas deploy <app>            # deploy now, logs printed live
$ sudo mas releases <app>
$ sudo mas rollback <app> [release]
$ sudo mas github status
```

Preview-Deployments für Pull Requests sind noch nicht implementiert; das Datenmodell (ein `environment`
pro App) ist dafür bereit.

## Den Server betreiben

### Ressourcen

Die Seite **Ressourcen** zeigt, was den Arbeitsspeicher und die CPU des Servers nutzt, alles auf einmal,
alle 10 Sekunden aktualisiert: eine Zeile pro Mas-App (alle ihre Instanzen), pro Compose-Projekt (Dienste
aus dem Katalog, Supabase, deine eigenen Compose-Dateien), pro sonstigem Systemdienst, Mas selbst und die Benutzer-Sessions,
sortiert nach Arbeitsspeicher, jeweils mit der CPU. Sie liest die cgroups von systemd
(`memory.current` ohne den inaktiven Datei-Cache, `cpu.stat`), sodass die Messung des ganzen Servers
fast nichts kostet.

Jede Node.js-, Next.js- oder Python-App hat eine Karte **Ressourcen** (auch von dieser Seite aus bearbeitbar):

- **Speicherlimit**: in ihre Units geschrieben (`MemoryHigh` bei 90 %, `MemoryMax`); Node-Apps bekommen ein
  passendes `--max-old-space-size`. Eine App mit Speicherleck wird langsamer und startet allein neu, statt den
  anderen den Arbeitsspeicher wegzunehmen.
- **Schlafen bei Inaktivität**: Nach N Minuten ohne Anfrage wird die App gestoppt und verbraucht nichts. Der
  nächste Besucher weckt sie über Mas auf und sieht so lange eine kurze „Startet“-Seite.
- **Instanzen folgen dem Traffic**: zwischen einem Minimum und einem Maximum eine Instanz mehr pro 600
  Anfragen pro Minute oder wenn die CPU über 70 % bleibt, eine weniger nach 10 ruhigen Minuten. Instanzen werden
  ohne Neubau gestartet und gestoppt.
- **Ratschläge** auf Basis dessen, was die App wirklich nutzt: ein Limit, das gesetzt werden sollte, ein zu knappes Limit, eine App, die
  schlafen könnte, Instanzen, die dem Traffic folgen könnten, `output: "standalone"` für Next.js, ein
  Minecraft-Container, der seinen ganzen Arbeitsspeicher reserviert. **Übernehmen** wendet einen an, **Alle Ratschläge anwenden**
  alle zusammen.

### Traffic-Statistiken

Die Seite jeder App und jeder Website des Servers hat eine Karte **Traffic**: Anfragen, Seitenaufrufe und
Besucher pro Tag (7 bis 365 Tage) und die meistaufgerufenen Seiten. Sie werden aus den nginx-Access-Logs gezählt,
inkrementell, ohne Cookies oder Skripte: Es wird keine IP-Adresse aufbewahrt, Bots, Bilder und Skripte
werden bei den Seitenaufrufen nicht mitgezählt, und ein Besucher wird einmal pro Tag gezählt. Websites ohne eigenes
Access-Log werden über `/var/log/nginx/mas-hosts.access.log` gezählt, das Mas für sie anlegt (jede Zeile
beginnt mit dem Namen der Website).

### Wartungsmodus

**Apps → Wartung** (oder die Karte **Wartung** einer App) versetzt eine Website in Wartestellung: Besucher bekommen eine
Seite deiner Wahl mit einem `503` und `Retry-After` (Suchmaschinen kommen später wieder), die Adressen,
die du erlaubst, sehen weiterhin die echte Website, und die App läuft weiter. Eine Wartung kann zu einer
festgelegten Zeit von selbst enden, und alle Websites können auf einmal in Wartung gehen (oder zurückkommen).

- **Seiten** schreibt Mas aus einem Titel, einer Nachricht, einer Rückkehrzeit und einer Farbe, oder sie werden **importiert**:
  eine HTML-Datei oder ein ZIP eines Ordners mit einer `index.html` und ihren Bildern, Styles und Schriften. Mas fügt
  ein `<base>` hinzu, damit relative Links von jeder URL der Website aus funktionieren. Jede Seite hat eine Vorschau und kann
  auf jeder Website verwendet werden.
- Das Einschalten schreibt den vhost nie um: Mas schreibt ein `geo` und ein `map` in
  `/etc/nginx/conf.d/mas-maintenance.conf` und ein Snippet, das der vhost einbindet, testet mit `nginx -t`
  und lädt neu; die Seite und die ACME-Challenges kommen immer durch.
- Es gilt für die Websites, deren nginx-Konfiguration Mas schreibt (Apps und Dienste mit einer Domain).

### Alles neu starten, identisch

**Übersicht → Alles neu starten** startet jeden laufenden Dienst genau so neu, wie er war, nachdem es
die Liste angezeigt hat (du kannst einige abwählen). Container werden über die Docker-API an Ort und Stelle neu gestartet, nie
neu erstellt: derselbe Container, dasselbe Image, dieselben Variablen, Volumes und Ports. Units starten mit
`systemctl restart` neu, PM2-Apps mit `pm2 restart`, dann die Mas-Apps Instanz für Instanz mit einem
Health-Check, und nginx wird zuletzt nach `nginx -t` neu geladen. Innerhalb eines Compose-Projekts kommen die Container,
von denen die anderen abhängen, zuerst. Mas vergleicht die Konfiguration jedes Dienstes vorher und nachher und
meldet *identisch* oder was sich geändert hat; was gestoppt war, bleibt gestoppt. Mas selbst, nginx, Docker und
SSH bleiben unangetastet.

### Health-Checks

`mas doctor` (CLI) und die Seite **Zustand** prüfen, nur lesend:

| Prüfung | Warum das wichtig ist |
|-------|----------------|
| Kaputte certbot-Lineage (`live/*/cert.pem` kein Symlink), Erneuerungskonfiguration ohne Dateien | certbot überspringt sie oder scheitert bei jedem Lauf daran |
| Abgelaufene / bald ablaufende Zertifikate | die Erneuerung schlägt irgendwo fehl |
| Zertifikate, die von keinem aktiven vhost genutzt werden | Überbleibsel, die `certbot renew` scheitern lassen |
| `standalone`-Erneuerungen mit `systemctl stop nginx`-Hooks | jede Erneuerung legt alle Websites lahm und kann nginx gestoppt zurücklassen |
| Fehlender Webroot, inaktiver `certbot.timer` | Erneuerungen können nicht gelingen |
| nginx läuft außerhalb von systemd | `systemctl reload` bewirkt nichts, Neustarts scheitern mit „address already in use“ |
| vhost zeigt auf ein fehlendes Zertifikat, `nginx -t` schlägt fehl | der nächste Reload legt jede Website lahm |
| Domains, die nicht aufgelöst werden oder auf einen anderen Server zeigen | Website umgezogen: ihr vhost/Zertifikat hier ist Ballast |

Zu jedem Problem gibt es eine Erklärung und, wenn es sicher ist, den Befehl, der es behebt. Mas wendet
diese Lösungen nie von selbst an.

## Mehrere Server (Cluster)

Die Seite **Server** macht aus mehreren Mas-Servern einen Cluster, gesteuert von einem Dashboard aus.

1. Installiere Mas auf jedem weiteren Server und mach ihn dann zu einem **Agent**: ohne Dashboard, er antwortet nur
   auf die Aufrufe des primären Servers über seine private Adresse (ein VPC-, WireGuard- oder Tailscale-Netzwerk, nie
   ein öffentliches).

   ```console
   $ sudo mas agent init --address 10.0.0.2      # prints the join token, shown once
   $ sudo mas agent token                        # another token later
   ```

2. Auf dem primären Server: **Server → Server hinzufügen** mit `http://10.0.0.2:7070` und dem Token. Tokens werden
   gehasht gespeichert, geben das Recht, Code auf diesem Server zu deployen, und können widerrufen werden.
3. Kreuze in der Karte **Lastverteilung** einer App die Server an, auf denen sie laufen soll. Jeder Deploy baut
   denselben Commit auf jedem davon (mit einem kurzlebigen Clone-Token aus der GitHub App des primären Servers), und
   nginx auf dem primären Server verteilt die Anfragen auf jede Kopie, die antwortet.

- **Live-Karte**: welcher Server welche Kopie betreibt, und die Anfragen der letzten Minute pro Backend.
- **Automatisches Auffüllen**: Bei einem Server, der 2 Minuten lang schweigt, werden seine Kopien auf einem anderen verfügbaren Server neu gebaut.
- **Wenn der primäre Server verloren geht**: Der primäre Server schickt laufend seinen Zustand (Apps, Einstellungen, verschlüsselt) an die
  Agents. Auf einem von ihnen macht `sudo mas agent promote` ihn zum primären Server: Seine Kopien werden zu den Apps,
  die übrigen Apps werden dort gebaut, und er steuert die verbleibenden Server. Lass die Domains auf ihn zeigen; kommt
  der frühere primäre Server zurück, verweigern die anderen seine Befehle (jede Beförderung erhöht eine Epoche).
- `sudo mas agent leave` macht aus einem Agent wieder ein normales Mas mit seinem Dashboard.

## So funktioniert die Erkennung

```
nginx vhost ──proxy_pass──▶ port ──ss──▶ PID ──/proc──▶ cgroup → manager (systemd / pm2 / docker)
                                                 └────▶ cwd    → project folder → type, git
nginx vhost ──fastcgi_pass + root──▶ PHP-FPM site
nginx vhost ──root only──▶ static site
listening port / pm2 app / container / unit without vhost ──▶ "no domain" service
```

| Collector       | Quelle                                                                 |
|-----------------|------------------------------------------------------------------------|
| `nginx`         | `/etc/nginx/sites-enabled/*`, `conf.d/*.conf`, Includes, Upstreams, Zertifikate |
| `ports`         | `ss -tlnpH` (TCP) und `ss -xlnpH` (Unix-Sockets)                       |
| `processes`     | `/proc/<pid>/{status,cmdline,cwd,cgroup,environ}`                      |
| `systemd`       | `systemctl list-units` / `systemctl show`, OS-Units herausgefiltert    |
| `pm2`           | `pm2 jlist` für jeden Benutzer mit einem `~/.pm2` (nur wenn sein Daemon läuft) |
| `docker`        | `/var/run/docker.sock` (sauber übersprungen, wenn nicht vorhanden)     |

Die „80 → 443“-Umleitungsblöcke von certbot und `www.`-Umleitungen werden mit dem HTTPS-Block derselben
Website zusammengeführt. Git-Metadaten (Remote, Branch, letzter Commit) werden direkt aus `.git/` gelesen, wobei
in die Remote-URL eingebettete Zugangsdaten entfernt werden.

Diensttypen werden durch geordnete Regeln bestimmt: `fastcgi_pass` oder `.php`-Dateien → **php**,
`package.json` mit `next` → **nextjs**, `package.json` → **node**,
`requirements.txt` / `pyproject.toml` → **python**, `root` ohne Proxy → **static**,
sonst **unknown**. Ein im Dashboard von Hand gesetztes Label gewinnt immer.

### Konfiguration

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

## Sicherheitsmodell

- **Netzwerk**: Die API lauscht nur auf `127.0.0.1:7070` (`mas serve` lehnt alles andere ab).
  Der öffentliche Einstiegspunkt ist der nginx-vhost: nur HTTPS, HSTS, `X-Frame-Options: DENY`, CSP,
  `client_max_body_size 2m`.
- **Authentifizierung**: Administratorkonten werden auf dem Server angelegt (`mas user create`), nie
  über das Web. CMS-Redakteurskonten werden von einem Administrator im Dashboard angelegt und können nur
  die CMS-Routen der Websites erreichen, die ihnen zugewiesen wurden. Passwörter werden mit argon2id gehasht. **TOTP-2FA** (RFC 6238, 30 s, ±1 Schritt), und
  jeder Code wird nur einmal akzeptiert (Schutz vor Wiederverwendung). Fünf fehlgeschlagene Anmeldungen pro Konto oder zehn pro
  IP innerhalb von 15 Minuten lösen eine vorübergehende Sperre aus (HTTP 429). Anmeldungen werden ins Audit-Log geschrieben.
- **Sessions**: zufälliges 256-Bit-Token in einem `HttpOnly`-, `Secure`-, `SameSite=Strict`-Cookie; gespeichert wird nur
  sein SHA-256. 12 h Inaktivitäts-Timeout, höchstens 7 Tage. Eine Änderung des Passworts oder des 2FA-Geräts
  meldet jede andere Session ab.
- **CSRF**: Jede verändernde Anfrage braucht den sessionspezifischen Header `X-CSRF-Token`, und ein fremder
  `Origin` wird abgelehnt.
- **Deployments**: Der GitHub-Webhook wird abgelehnt, sofern sein `X-Hub-Signature-256`-HMAC nicht gültig ist,
  noch bevor die Payload überhaupt geparst wird. Apps laufen als eigener Benutzer ohne root, lauschen auf Loopback und
  bekommen ihre Secrets aus einer `0600`-Umgebungsdatei; Secrets werden in der Datenbank verschlüsselt gespeichert.
  Von dir konfigurierte Build-Befehle laufen als dieser unprivilegierte Benutzer. Generierte Dateien werden gesichert,
  bevor sie ersetzt werden, und nginx wird erst nach einem erfolgreichen `nginx -t` neu geladen.
- **Härtung aus dem Security-Review**: Build-/Startbefehle sind einzeilig (keine Injektion von systemd-
  Direktiven), der ausgelieferte Ordner kann das Release nicht verlassen (`..` abgelehnt), GitHub-Pfade
  und -Refs werden validiert und URL-kodiert, jede Webhook-Zustellung wird nur einmal akzeptiert, das Ersetzen des 2FA-
  Geräts erfordert einen aktuellen Code, Passwortprüfungen innerhalb einer Session sind ratenbegrenzt, das Session-
  Cookie hat das Präfix `__Host-`, die Header `Permissions-Policy` und `Cross-Origin-Opener-Policy` sind
  gesetzt, und App-Units laufen ohne Capabilities (`CapabilityBoundingSet=`), `PrivateDevices`,
  `ProtectKernel*`, `RestrictSUIDSGID`.
- **Aktionen**: strikte Allow-List `(manager, action) → argv`, gebildet aus Bezeichnern, die der
  Scan gefunden hat (mit regulären Ausdrücken erneut validiert), ohne Shell ausgeführt. Stoppen und Neustarten verlangen eine
  Bestätigung. Jede Aktion wird mit ihrem Befehl und ihrer Ausgabe ins Audit-Log geschrieben.
- **nginx**: Mas lädt nur nach einem erfolgreichen `nginx -t` neu. Der Installer sichert jede Datei, die er
  ersetzt (`<file>.mas-bak-<timestamp>`), und stellt sie wieder her, wenn `nginx -t` fehlschlägt.
- **Nur lesende Erkennung**: Scans schreiben nie. Die Testsuite hasht den gesamten Fixture-Baum
  vor und nach einem Scan und führt jeden ausgeführten Befehl auf einer Allow-List. `pm2 jlist` wird nur aufgerufen,
  wenn der Daemon des Benutzers bereits läuft (sonst würde es einen starten), und zwar als dieser Benutzer.
- **Secrets**, die unterwegs gefunden werden (Zugangsdaten in Git-Remotes, PM2-Umgebungsvariablen), werden
  weder gespeichert noch angezeigt. Die Datenbank (`/var/lib/mas/mas.db`) ist `0600`, nur für root.
- **Cluster**: Beitritts-Tokens sind zufällig, gehasht gespeichert und widerrufbar; Server kommunizieren nur über das private
  Netzwerk; der an die Agents gesendete Zustand ist verschlüsselt, und die Epoche eines beförderten Servers lässt die
  anderen einen früheren primären Server ablehnen.
- **Wartungsseiten** sind öffentliche Dateien (jeder Besucher einer Website in Wartung bekommt sie): Das
  Dashboard zeigt ihre Vorschau in einem abgeschotteten Frame ohne Skripte; importierte ZIPs können nicht außerhalb
  ihres Ordners schreiben oder versteckte Dateien hinzufügen.
- **Verlorenes Smartphone**: `sudo mas user 2fa-reset` auf dem Server. Root auf dem Server ist der
  Weg zur Wiederherstellung.
- **Warum der Agent als root läuft**: Er muss `/proc` für jeden Benutzer, `ss -p` und den Docker-
  Socket lesen und Dienste neu starten. `mas.service` begrenzt den möglichen Schaden mit
  `NoNewPrivileges=yes`, `ProtectSystem=full`, `PrivateTmp=yes`, `ProtectKernel*` und
  `ReadWritePaths=`, beschränkt auf `/var/lib/mas /srv/mas /etc/nginx /etc/mas /etc/systemd/system
  /etc/letsencrypt`.

## Beta: bekannte Einschränkungen

- Nur Debian (und Derivate) mit nginx; Apache-/Caddy-Server werden nicht erkannt.
- Administratorkonten werden auf dem Server angelegt; noch keine 2FA-Wiederherstellungscodes (`sudo mas user 2fa-reset`).
- Noch keine Preview-Deployments für Pull Requests; ein Produktions-Branch pro App.
- Andere DNS-Anbieter als OVH und Cloudflare sind anhand ihrer API-Dokumentation implementiert und
  noch nicht praxiserprobt: Bitte melde Probleme.
- Die CSP des Dashboards behält `'unsafe-inline'` (vom statischen Next.js-Export benötigt).
- Ressourcen und Traffic brauchen cgroup v2 (Debian 11+): Auf älteren Systemen werden nur Mas-Apps gemessen.

## Entwicklung

```console
$ python -m venv .venv && .venv/bin/pip install -e ".[dev]"
$ .venv/bin/pytest
$ cd web && npm ci && npm run build      # dashboard → web/out
```

Die Tests brauchen keinen echten Server. `tests/fixtures/server.py` baut einen gefälschten Debian-Baum (nginx-
Konfigurationen, `/proc`, Ausgaben von `ss` und `systemctl`, eine PM2-jlist, Antworten der Docker-API), und
`FixtureHost` stellt ihn den Collectors bereit. Du kannst die ganze App dagegen laufen lassen:

```console
$ python tests/fixtures/server.py /tmp/mas-demo
$ mas --fixture /tmp/mas-demo --db /tmp/mas-demo.db user create admin
$ mas --fixture /tmp/mas-demo --db /tmp/mas-demo.db serve --insecure-cookie
```

## Roadmap

- [x] **Phase 1: Erkennung.** Collectors, Korrelation, Typerkennung, `mas scan [--json]`.
- [x] **Phase 2: Dashboard und Verwaltung.** API, 2FA, Hintergrund-Scan, Logs, Aktionen per Allow-List,
      Labels, Audit-Log, `install.sh`, `mas.service`, `mas self-update`.
- [x] **Phase 2.5: Health-Checks.** Zertifikate, certbot-Erneuerung, nginx, DNS (`mas doctor`).
- [x] **Phase 3: Deployen von GitHub.** GitHub App (Manifest-Flow), signierte Webhooks, atomare
      Releases, generierte systemd-Unit und nginx-vhost, certbot, Health-Check mit automatischem
      Rollback, 5 behaltene Releases, Live-Build-Logs (SSE), Übernahme erkannter Dienste.
- [x] **Lastverteilung auf einem Server**: mehrere Instanzen pro App, externe Backends, Rolling Restarts.
- [x] **Dienste, Supabase und CMS**: fertige Dienste mit Backups, selbst gehostetes Supabase, ein CMS
      für Kunden, Traffic-Statistiken, Dashboard in vier Sprachen.
- [x] **Cluster**: Agents, Kopien von Apps auf mehreren Servern, Live-Karte, automatisches Auffüllen, Beförderung.
- [x] **Ressourcen und Betrieb**: Seite Ressourcen, Speicherlimits, Schlafen bei Inaktivität, Autoscaling,
      Ratschläge, alles identisch neu starten, Wartungsmodus.
- [ ] Als Nächstes: Preview-Deployments pro Pull Request, Wiederherstellungscodes für 2FA, Benachrichtigungen.

## Lizenz

MIT
