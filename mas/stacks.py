"""Ready-made services deployed with Docker: a mail server, a Minecraft server, Uptime Kuma…

A service is an app with ``mode = "stack"``: no repository, a template from the catalog below and
the options filled in the dashboard (``apps.stack``, JSON), its generated passwords in the encrypted
``apps.env``. Deploying writes ``/srv/mas/stacks/<id>/docker-compose.yml`` from the template (a Python
dict dumped as YAML: option values only ever land in environment values, never in a shell) and runs
``docker compose up -d``; its data lives in ``/srv/mas/stacks/<id>/data``.

Websites (Uptime Kuma, n8n…) listen on 127.0.0.1 and nginx serves their domain with HTTPS, like any
app. Ports meant for the outside (Minecraft 25565, SMTP 25…) are published on every interface:
Docker opens them in the firewall by itself.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from mas.config import Config
from mas.db import Database
from mas.deploy import DOMAIN_RE, DeployError, ReleaseLog, allocate_port, app_paths, used_ports
from mas.dns import server_ips
from mas.envcrypt import decrypt_env, encrypt_env, load_fernet
from mas.host import Host
from mas.models import ServiceType

log = logging.getLogger(__name__)

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]*$")
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PORT_SPEC_RE = re.compile(r"^(\d{1,5}):(\d{1,5})(/(tcp|udp))?$")
MAILBOX_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}$")
RESERVED_PORTS = {22, 53, 80, 443, 7070}


class StackError(Exception):
    pass


@dataclass
class Ctx:
    """What a template needs to write its compose file."""
    id: str
    options: dict
    secrets: dict[str, str]
    data: str  # data folder, as Docker sees it (real path)
    files: str  # the same folder for Host reads (virtual path)
    web_port: int | None
    ports: dict[str, int]
    domain: str | None


@dataclass
class Port:
    name: str
    container: int
    protocol: str = "tcp"
    label: str = ""
    fixed: bool = False  # SMTP must stay on 25: the outside world expects it


@dataclass
class Template:
    id: str
    name: str
    category: str  # games | mail | web | other
    description: str
    compose: Callable[[Ctx], dict]
    fields: list[dict] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    web: int | None = None  # container port served by nginx on the app's domain
    needs_domain: bool = False
    domain_label: str = "Domain"
    secrets: tuple[str, ...] = ()
    ram: Callable[[dict], int] = lambda o: 256
    owner: str | None = None  # uid:gid of the data folder, for images that do not run as root
    info: Callable[[Ctx, Host, str | None], dict] | None = None  # connection details, DNS records
    after: Callable[["StackManager", dict, Ctx, ReleaseLog], None] | None = None  # once the containers run
    actions: dict[str, dict] = field(default_factory=dict)
    image: str = ""

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "category": self.category, "description": self.description,
                "fields": self.fields, "needs_domain": self.needs_domain, "domain_label": self.domain_label,
                "web": self.web is not None, "image": self.image,
                "ports": [{"name": p.name, "container": p.container, "protocol": p.protocol, "label": p.label,
                           "fixed": p.fixed} for p in self.ports],
                "ram_mb": self.ram({f["key"]: f.get("default") for f in self.fields}),
                "actions": [{"id": k, "label": a["label"], "fields": a.get("fields", [])}
                            for k, a in self.actions.items()]}


def _service(image: str, ctx: Ctx, web: int | None = None, **extra) -> dict:
    """One compose service: restarted on failure, its web port on 127.0.0.1 only."""
    ports = list(extra.pop("ports", []))
    if web and ctx.web_port:
        ports.insert(0, f"127.0.0.1:{ctx.web_port}:{web}")
    return {"image": image, "restart": "unless-stopped", **extra, **({"ports": ports} if ports else {})}


# -- games --------------------------------------------------------------------------

MEMORY = ["1G", "2G", "3G", "4G", "6G", "8G"]


def _minecraft(ctx: Ctx) -> dict:
    o = ctx.options
    env = {"EULA": "TRUE", "TYPE": o["server_type"], "VERSION": o["version"] or "LATEST", "MEMORY": o["memory"],
           "DIFFICULTY": o["difficulty"], "MODE": o["mode"], "MAX_PLAYERS": str(o["max_players"]),
           "MOTD": o["motd"], "ONLINE_MODE": "TRUE" if o["online_mode"] else "FALSE"}
    if o.get("whitelist"):
        env.update(ENABLE_WHITELIST="TRUE", ENFORCE_WHITELIST="TRUE", WHITELIST=o["whitelist"])
    if o.get("ops"):
        env["OPS"] = o["ops"]
    return {"services": {"minecraft": _service(
        "itzg/minecraft-server:latest", ctx, environment=env, tty=True, stdin_open=True,
        ports=[f"{ctx.ports['game']}:25565/tcp"], volumes=[f"{ctx.data}:/data"], stop_grace_period="60s")}}


def _bedrock(ctx: Ctx) -> dict:
    o = ctx.options
    env = {"EULA": "TRUE", "VERSION": "LATEST", "SERVER_NAME": o["motd"], "GAMEMODE": o["mode"],
           "DIFFICULTY": o["difficulty"], "MAX_PLAYERS": str(o["max_players"]),
           "ONLINE_MODE": "true" if o["online_mode"] else "false"}
    if o.get("whitelist"):
        env.update(ALLOW_LIST="true", ALLOW_LIST_USERS=o["whitelist"])
    return {"services": {"bedrock": _service(
        "itzg/minecraft-bedrock-server:latest", ctx, environment=env, tty=True, stdin_open=True,
        ports=[f"{ctx.ports['game']}:19132/udp"], volumes=[f"{ctx.data}:/data"])}}


def _game_info(ctx: Ctx, host: Host, ip: str | None) -> dict:
    port = ctx.ports["game"]
    address = ctx.domain or ip or "<server address>"
    shown = address if port in (25565, 19132) else f"{address}:{port}"
    return {"rows": [{"label": "Server address", "value": shown, "copy": True}],
            "notes": [{"text": "Players add this address in Multiplayer → Add server.", "vars": {}}]}


GAME_FIELDS = [
    {"key": "motd", "label": "Server name", "type": "text", "default": "A Mas server", "max": 60},
    {"key": "mode", "label": "Game mode", "type": "select", "default": "survival",
     "options": ["survival", "creative", "adventure"]},
    {"key": "difficulty", "label": "Difficulty", "type": "select", "default": "normal",
     "options": ["peaceful", "easy", "normal", "hard"]},
    {"key": "max_players", "label": "Players at most", "type": "number", "default": 10, "min": 1, "max": 200},
    {"key": "online_mode", "label": "Only players with an official account", "type": "boolean", "default": True},
    {"key": "whitelist", "label": "Allowed players (empty: everyone)", "type": "text", "default": "", "max": 1000,
     "help": "Player names separated by commas"},
]
EULA = {"key": "eula", "label": "I accept the Minecraft EULA (minecraft.net/eula)", "type": "boolean",
        "default": False, "required": True}


# -- mail ---------------------------------------------------------------------------

def _mail(ctx: Ctx) -> dict:
    domain = ctx.options["mail_domain"]
    d = ctx.data
    env = {"SSL_TYPE": "letsencrypt", "ONE_DIR": "1", "PERMIT_DOCKER": "none", "SPOOF_PROTECTION": "1",
           "POSTMASTER_ADDRESS": f"postmaster@{domain}", "ENABLE_FAIL2BAN": "1", "ENABLE_CLAMAV": "0",
           "ENABLE_SPAMASSASSIN": "0", "ENABLE_RSPAMD": "0", "ENABLE_AMAVIS": "0", "ENABLE_OPENDKIM": "1",
           "ENABLE_OPENDMARC": "1", "ENABLE_POLICYD_SPF": "1"}
    return {"services": {"mailserver": _service(
        "ghcr.io/docker-mailserver/docker-mailserver:latest", ctx, hostname=ctx.domain, environment=env,
        cap_add=["NET_ADMIN"], stop_grace_period="1m",
        ports=[f"{ctx.ports[p]}:{c}" for p, c in (("smtp", 25), ("submissions", 465), ("submission", 587),
                                                  ("imaps", 993))],
        volumes=[f"{d}/mail-data:/var/mail", f"{d}/mail-state:/var/mail-state", f"{d}/mail-logs:/var/log/mail",
                 f"{d}/config:/tmp/docker-mailserver", "/etc/letsencrypt:/etc/letsencrypt:ro",
                 "/etc/localtime:/etc/localtime:ro"])}}


def dkim_record(text: str) -> str | None:
    """``mail._domainkey IN TXT ( "v=DKIM1; …" "p=…" )`` → the TXT value."""
    value = "".join(re.findall(r'"([^"]*)"', text)).strip()
    return value or None


def _mail_info(ctx: Ctx, host: Host, ip: str | None) -> dict:
    domain, hostname = ctx.options["mail_domain"], ctx.domain
    dkim = None
    folder = f"{ctx.files}/config/opendkim/keys/{domain}"
    for name in sorted(host.listdir(folder)) if host.is_dir(folder) else []:
        if name.endswith(".txt"):
            dkim = dkim_record(host.read_text(f"{folder}/{name}") or "")
            break
    return {
        "rows": [
            {"label": "Mailbox", "value": f"{ctx.options['mailbox']}@{domain}", "copy": True},
            {"label": "Password", "value": ctx.secrets.get("mailbox_password", ""), "secret": True},
            {"label": "Incoming (IMAP)", "value": f"{hostname}:993 (SSL/TLS)", "copy": True},
            {"label": "Outgoing (SMTP)", "value": f"{hostname}:465 (SSL/TLS) or 587 (STARTTLS)", "copy": True},
        ],
        "dns": [
            {"type": "A", "name": hostname, "value": ip or "<server IP>"},
            {"type": "MX", "name": domain, "value": f"10 {hostname}"},
            {"type": "TXT", "name": domain, "value": "v=spf1 mx -all"},
            {"type": "TXT", "name": f"mail._domainkey.{domain}", "value": dkim or "(created at the first start)"},
            {"type": "TXT", "name": f"_dmarc.{domain}",
             "value": f"v=DMARC1; p=quarantine; rua=mailto:postmaster@{domain}"},
        ],
        "notes": [
            {"text": "Ask your hosting provider for the reverse DNS (PTR) of {ip}: {host}. Without it, Gmail and "
                     "Outlook put your mail in spam.", "vars": {"ip": ip or "?", "host": hostname}},
            {"text": "Many providers block outgoing port 25 on new servers: open a ticket if mail does not leave.",
             "vars": {}},
        ],
    }


def _mail_after(mgr: "StackManager", app: dict, ctx: Ctx, rlog: ReleaseLog) -> None:
    domain = ctx.options["mail_domain"]
    if not mgr.host.exists(f"{ctx.files}/config/postfix-accounts.cf"):
        address = f"{ctx.options['mailbox']}@{domain}"
        rlog.line(f"creating the mailbox {address}")
        mgr.exec(app, "mailserver", ["setup", "email", "add", address, ctx.secrets["mailbox_password"]], retries=20)
    if not mgr.host.is_dir(f"{ctx.files}/config/opendkim/keys/{domain}"):
        rlog.line("creating the DKIM key")
        mgr.exec(app, "mailserver", ["setup", "config", "dkim", "domain", domain], retries=5)
        mgr.compose(app, ["restart"], rlog)
    mgr._wait(app, rlog, timeout=300, healthy=True)
    rlog.line("checking that the mailbox accepts its password")
    _mail_ready(mgr, app, f"{ctx.options['mailbox']}@{domain}", ctx.secrets["mailbox_password"])


def _mail_ready(mgr: "StackManager", app: dict, address: str, password: str) -> None:
    """docker-mailserver picks up new accounts a few seconds later: wait until Dovecot accepts it."""
    mgr.exec(app, "mailserver", ["doveadm", "auth", "test", address, password], retries=30)


def _mail_add(mgr: "StackManager", app: dict, ctx: Ctx, values: dict) -> str:
    local = str(values.get("name", "")).strip().lower()
    if not MAILBOX_RE.fullmatch(local):
        raise StackError("mailbox name: letters, digits, . _ + -")
    password = secrets.token_urlsafe(12)
    address = f"{local}@{ctx.options['mail_domain']}"
    mgr.exec(app, "mailserver", ["setup", "email", "add", address, password])
    _mail_ready(mgr, app, address, password)
    return f"{address} created. Password: {password}"


# -- websites -----------------------------------------------------------------------

def _uptime_kuma(ctx: Ctx) -> dict:
    return {"services": {"uptime-kuma": _service("louislam/uptime-kuma:1", ctx, 3001,
                                                 volumes=[f"{ctx.data}:/app/data"])}}


def _vaultwarden(ctx: Ctx) -> dict:
    env = {"DOMAIN": f"https://{ctx.domain}", "SIGNUPS_ALLOWED": "true" if ctx.options["signups"] else "false",
           "ADMIN_TOKEN": ctx.secrets["admin_token"]}
    return {"services": {"vaultwarden": _service("vaultwarden/server:latest", ctx, 80, environment=env,
                                                 volumes=[f"{ctx.data}:/data"])}}


def _n8n(ctx: Ctx) -> dict:
    env = {"N8N_HOST": ctx.domain, "N8N_PROTOCOL": "https", "N8N_PORT": "5678",
           "WEBHOOK_URL": f"https://{ctx.domain}/", "GENERIC_TIMEZONE": ctx.options["timezone"],
           "TZ": ctx.options["timezone"]}
    return {"services": {"n8n": _service("docker.n8n.io/n8nio/n8n:latest", ctx, 5678, environment=env,
                                         volumes=[f"{ctx.data}:/home/node/.n8n"])}}


def _gitea(ctx: Ctx) -> dict:
    env = {"USER_UID": "1000", "USER_GID": "1000", "GITEA__server__DOMAIN": ctx.domain,
           "GITEA__server__ROOT_URL": f"https://{ctx.domain}/", "GITEA__server__DISABLE_SSH": "true",
           "GITEA__service__DISABLE_REGISTRATION": "false" if ctx.options["signups"] else "true"}
    return {"services": {"gitea": _service("gitea/gitea:latest", ctx, 3000, environment=env,
                                           volumes=[f"{ctx.data}:/data"])}}


def _web_info(rows: Callable[[Ctx], list[dict]] | None = None):
    def info(ctx: Ctx, host: Host, ip: str | None) -> dict:
        return {"rows": [{"label": "Address", "value": f"https://{ctx.domain}", "link": True},
                         *(rows(ctx) if rows else [])], "notes": []}
    return info


def parse_env_lines(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not ENV_KEY_RE.fullmatch(key.strip()):
            raise StackError(f"environment: {line[:40]!r} is not KEY=value")
        out.append((key.strip(), value.strip()))
    return out


def _custom(ctx: Ctx) -> dict:
    o = ctx.options
    ports = [s.strip() for s in o.get("public_ports", "").split(",") if s.strip()]
    volumes = [f"{ctx.data}:{o['volume']}"] if o.get("volume") else []
    extra = {"environment": dict(parse_env_lines(o.get("env", "")))} if o.get("env") else {}
    if volumes:
        extra["volumes"] = volumes
    return {"services": {"app": _service(o["image"], ctx, int(o.get("web_port") or 0) or None, ports=ports,
                                         **extra)}}


def _custom_info(ctx: Ctx, host: Host, ip: str | None) -> dict:
    rows = [{"label": "Address", "value": f"https://{ctx.domain}", "link": True}] if ctx.domain else []
    rows += [{"label": "Published port", "value": f"{ip or '<server IP>'}:{p}", "copy": True}
             for p in ctx.ports.values()]
    return {"rows": rows, "notes": []}


SIGNUPS = {"key": "signups", "label": "Anyone can create an account", "type": "boolean", "default": False,
           "help": "Create your own account first, then turn this off"}

TEMPLATES: dict[str, Template] = {t.id: t for t in (
    Template("minecraft", "Minecraft server (Java)", "games",
             "A Minecraft Java Edition server for you and your friends: Paper, Vanilla, Fabric or Forge.",
             _minecraft, image="itzg/minecraft-server",
             fields=[EULA,
                     {"key": "server_type", "label": "Server", "type": "select", "default": "PAPER",
                      "options": ["PAPER", "VANILLA", "FABRIC", "FORGE", "PURPUR"],
                      "help": "Paper: fast, with plugins. Fabric or Forge: mods."},
                     {"key": "version", "label": "Minecraft version", "type": "text", "default": "LATEST",
                      "max": 20, "help": "LATEST or a version such as 1.21.4"},
                     {"key": "memory", "label": "Memory", "type": "select", "default": "2G", "options": MEMORY},
                     *GAME_FIELDS,
                     {"key": "ops", "label": "Operators", "type": "text", "default": "", "max": 500,
                      "help": "Player names with every right, separated by commas"}],
             ports=[Port("game", 25565, "tcp", "Players connect on")],
             ram=lambda o: int(str(o.get("memory") or "2G").rstrip("G")) * 1024 + 512, info=_game_info),
    Template("minecraft-bedrock", "Minecraft server (Bedrock)", "games",
             "For players on phones, consoles and Windows (Bedrock Edition).",
             _bedrock, image="itzg/minecraft-bedrock-server", fields=[EULA, *GAME_FIELDS],
             ports=[Port("game", 19132, "udp", "Players connect on")], ram=lambda o: 1024, info=_game_info),
    Template("mail", "Mail server", "mail",
             "Your own email addresses (contact@yourdomain…) read in any mail app: docker-mailserver, with "
             "anti-spoofing, DKIM, SPF and DMARC.",
             _mail, image="docker-mailserver", needs_domain=True, domain_label="Mail server address",
             fields=[{"key": "mail_domain", "label": "Email domain", "type": "text", "default": "",
                      "required": True, "max": 253, "help": "The part after @, for example example.com"},
                     {"key": "mailbox", "label": "First mailbox", "type": "text", "default": "contact",
                      "required": True, "max": 64, "help": "contact → contact@example.com"}],
             ports=[Port("smtp", 25, "tcp", "SMTP (receiving)", fixed=True),
                    Port("submissions", 465, "tcp", "SMTP SSL (sending)", fixed=True),
                    Port("submission", 587, "tcp", "SMTP STARTTLS (sending)", fixed=True),
                    Port("imaps", 993, "tcp", "IMAP SSL (reading)", fixed=True)],
             secrets=("mailbox_password",), ram=lambda o: 512, info=_mail_info, after=_mail_after,
             actions={"add-mailbox": {"label": "Add a mailbox", "run": _mail_add,
                                      "fields": [{"key": "name", "label": "Name before @", "type": "text"}]}}),
    Template("uptime-kuma", "Uptime Kuma", "web",
             "Watches your sites and services and alerts you (email, Discord, Telegram…) when one is down.",
             _uptime_kuma, image="louislam/uptime-kuma", web=3001, needs_domain=True, ram=lambda o: 200,
             info=_web_info()),
    Template("vaultwarden", "Vaultwarden", "web",
             "A password manager for you and your team, compatible with the Bitwarden apps.",
             _vaultwarden, image="vaultwarden/server", web=80, needs_domain=True, fields=[SIGNUPS],
             secrets=("admin_token",), ram=lambda o: 100,
             info=_web_info(lambda c: [{"label": "Admin page", "value": f"https://{c.domain}/admin", "link": True},
                                       {"label": "Admin token", "value": c.secrets.get("admin_token", ""),
                                        "secret": True}])),
    Template("n8n", "n8n", "web", "Automate tasks between your apps (like Zapier), with a visual editor.",
             _n8n, image="n8nio/n8n", web=5678, needs_domain=True, owner="1000:1000", ram=lambda o: 400,
             fields=[{"key": "timezone", "label": "Time zone", "type": "text", "default": "Europe/Paris", "max": 60}],
             info=_web_info()),
    Template("gitea", "Gitea", "web", "Your own GitHub: Git repositories, issues and pull requests.",
             _gitea, image="gitea/gitea", web=3000, needs_domain=True, fields=[SIGNUPS], ram=lambda o: 250,
             info=_web_info()),
    Template("custom", "Any Docker image", "other",
             "Run an image from Docker Hub or another registry: its web port served on a domain and/or ports "
             "published as they are.",
             _custom, image="",
             fields=[{"key": "image", "label": "Image", "type": "text", "default": "", "required": True, "max": 200,
                      "help": "For example nginx:alpine or ghcr.io/owner/app:1.2"},
                     {"key": "web_port", "label": "Web port in the container (0: none)", "type": "number",
                      "default": 0, "min": 0, "max": 65535},
                     {"key": "public_ports", "label": "Published ports", "type": "text", "default": "", "max": 300,
                      "help": "host:container, comma separated, for example 25565:25565, 19132:19132/udp"},
                     {"key": "volume", "label": "Folder kept between updates (in the container)", "type": "text",
                      "default": "", "max": 200, "help": "For example /data"},
                     {"key": "env", "label": "Environment", "type": "textarea", "default": "", "max": 5000,
                      "help": "KEY=value, one per line"}],
             ram=lambda o: 256, info=_custom_info),
)}


def check_options(template: Template, raw: dict, previous: dict | None = None) -> dict:
    """Values of the template's fields, checked; unknown keys are dropped."""
    out = {}
    for f in template.fields:
        value = raw.get(f["key"], (previous or {}).get(f["key"], f.get("default")))
        label, kind = f["label"], f["type"]
        if kind == "boolean":
            value = bool(value)
            if f.get("required") and not value:
                raise StackError(f"{label}: required")
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
                raise StackError(f"{label}: a whole number")
            value = int(value)
            if not f.get("min", value) <= value <= f.get("max", value):
                raise StackError(f"{label}: between {f.get('min')} and {f.get('max')}")
        elif kind == "select":
            if value not in f["options"]:
                raise StackError(f"{label}: one of {', '.join(f['options'])}")
        else:
            value = str(value or "").strip()
            if len(value) > f.get("max", 200) or (kind == "text" and not TEXT_RE.fullmatch(value)):
                raise StackError(f"{label}: too long or forbidden characters")
            if f.get("required") and not value:
                raise StackError(f"{label}: required")
        out[f["key"]] = value
    if template.id == "mail":
        out["mail_domain"], out["mailbox"] = out["mail_domain"].lower(), out["mailbox"].lower()
        if not DOMAIN_RE.fullmatch(out["mail_domain"]):
            raise StackError(f"Email domain: {out['mail_domain']} is not a domain")
        if not MAILBOX_RE.fullmatch(out["mailbox"]):
            raise StackError("First mailbox: letters, digits, . _ + -")
    if template.id == "custom":
        if not IMAGE_RE.fullmatch(out["image"]):
            raise StackError(f"Image: {out['image']!r} is not an image name")
        parse_env_lines(out["env"])
        for spec in (s.strip() for s in out["public_ports"].split(",") if s.strip()):
            m = PORT_SPEC_RE.fullmatch(spec)
            if not m or not 0 < int(m.group(1)) < 65536 or not 0 < int(m.group(2)) < 65536:
                raise StackError(f"Published ports: {spec!r} is not host:container")
        if out["volume"] and not re.fullmatch(r"/[A-Za-z0-9._/-]*", out["volume"]):
            raise StackError("Folder: an absolute path such as /data")
    return out


class StackManager:
    def __init__(self, host: Host, config: Config, db: Database, deployer) -> None:
        self.host, self.config, self.db, self.deployer = host, config, db, deployer
        self.root = str(PurePosixPath(config.apps_dir).parent / "stacks")
        deployer.mode_handlers["stack"] = self.deploy
        deployer.remove_handlers["stack"] = self.remove

    # -- state -------------------------------------------------------------------------

    @staticmethod
    def template_of(app: dict) -> tuple[Template, dict]:
        spec = json.loads(app.get("stack") or "{}")
        template = TEMPLATES.get(spec.get("template"))
        if template is None:
            raise StackError(f"unknown service template {spec.get('template')!r}")
        return template, spec

    def dir(self, app_id: str) -> str:
        return f"{self.root}/{app_id}"

    def ctx(self, app: dict) -> Ctx:
        _, spec = self.template_of(app)
        env = decrypt_env(load_fernet(self.host, self.config.secret_key_path), app["env"])
        files = f"{self.dir(app['id'])}/data"
        return Ctx(app["id"], spec.get("options", {}), env, str(self.host.real(files)), files, app["port"],
                   {k: int(v) for k, v in spec.get("ports", {}).items()}, app["domain"])

    def _cmd(self, app: dict, args: list[str]) -> list[str]:
        folder = self.dir(app["id"])
        return ["docker", "compose", "-p", f"mas-{app['id']}", "--project-directory", str(self.host.real(folder)),
                "-f", str(self.host.real(f"{folder}/docker-compose.yml")), *args]

    def compose(self, app: dict, args: list[str], rlog: ReleaseLog | None = None, timeout: float = 900) -> str:
        lines: list[str] = []

        def out(line: str) -> None:
            lines.append(line)
            if rlog:
                rlog.line(line)
        rc = self.host.stream(self._cmd(app, args), out, timeout=timeout)
        if rc is None:
            raise StackError("Docker is not installed on this server (with its compose plugin)")
        if rc != 0:
            raise StackError(f"docker compose {args[0]} failed" + (f": {' '.join(lines)[-500:]}" if lines else ""))
        return "\n".join(lines)

    def exec(self, app: dict, service: str, args: list[str], retries: int = 1) -> str:
        argv = self._cmd(app, ["exec", "-T", service, *args])
        last = ""
        for attempt in range(retries):
            res = self.host.run(argv, timeout=120)
            if res is not None and res.ok:
                return res.stdout
            last = (res.stderr or res.stdout).strip()[-300:] if res is not None else "docker missing"
            if attempt < retries - 1:
                time.sleep(3)
        raise StackError(f"{' '.join(args[:3])} failed: {last}")

    # -- creating ----------------------------------------------------------------------

    def _udp_ports(self) -> set[int]:
        res = self.host.run(["ss", "-ulnH"])
        found = set()
        for line in (res.stdout.splitlines() if res is not None and res.ok else []):
            parts = line.split()
            if len(parts) >= 4 and parts[3].rsplit(":", 1)[-1].isdigit():
                found.add(int(parts[3].rsplit(":", 1)[-1]))
        return found

    def _stack_ports(self) -> set[int]:
        return {int(v) for a in self.db.list_apps() if a["mode"] == "stack"
                for v in json.loads(a.get("stack") or "{}").get("ports", {}).values()}

    def check_ports(self, template: Template, wanted: dict, options: dict, own: set[int] = frozenset()) -> dict:
        busy = (used_ports(self.host, self.db) | self._udp_ports() | self._stack_ports()) - set(own)
        out: dict[str, int] = {}
        for p in template.ports:
            port = p.container if p.fixed else int(wanted.get(p.name) or p.container)
            if not 0 < port < 65536 or port in RESERVED_PORTS:
                raise StackError(f"{p.label or p.name}: port {port} cannot be used")
            if port in busy:
                raise StackError(f"port {port} is already used on this server")
            out[p.name] = port
        if template.id == "custom":
            for spec in (s.strip() for s in options.get("public_ports", "").split(",") if s.strip()):
                port = int(PORT_SPEC_RE.fullmatch(spec).group(1))
                if port in RESERVED_PORTS or port in busy:
                    raise StackError(f"port {port} is already used on this server")
                out[f"custom-{port}"] = port
        return out

    def create(self, template_id: str, app_id: str, options: dict, ports: dict, domain: str | None,
               actor: str) -> dict:
        template = TEMPLATES.get(template_id)
        if template is None:
            raise StackError(f"unknown service {template_id!r}")
        if not ID_RE.fullmatch(app_id):
            raise StackError("name: 2-24 characters, lowercase letters, digits and -, starting with a letter")
        if self.db.get_app(app_id):
            raise StackError(f"{app_id} already exists")
        domain = (domain or "").strip().lower() or None
        if domain and not DOMAIN_RE.fullmatch(domain):
            raise StackError(f"invalid domain: {domain}")
        if template.needs_domain and not domain:
            raise StackError(f"{template.domain_label}: required")
        if domain and any(a["domain"] == domain for a in self.db.list_apps()):
            raise StackError(f"{domain} is already used by another app")
        checked = check_options(template, options)
        published = self.check_ports(template, ports, checked)
        web = template.web or (int(checked.get("web_port") or 0) if template.id == "custom" else None)
        port = allocate_port(self.host, self.config, self.db) if web else None
        env = {name: secrets.token_urlsafe(18) for name in template.secrets}
        fernet = load_fernet(self.host, self.config.secret_key_path)
        self.db.create_app(app_id, name=template.name, repo="", branch="", installation_id=None, domain=domain,
                           type=(ServiceType.NODE if web else ServiceType.UNKNOWN).value, port=port, mode="stack",
                           env=encrypt_env(fernet, env) if env else None, auto_deploy=0,
                           stack=json.dumps({"template": template.id, "options": checked, "ports": published}))
        self.db.audit(actor, "stack-create", True, service_id=app_id,
                      detail=f"{template.id} → {domain or 'no domain'}")
        return {"id": app_id, "release_id": self.deployer.enqueue(app_id, "create", actor)}

    def update(self, app_id: str, options: dict, ports: dict, actor: str) -> int:
        app = self.db.get_app(app_id)
        template, spec = self.template_of(app)
        checked = check_options(template, options, spec.get("options"))
        own = {int(v) for v in spec.get("ports", {}).values()}
        published = self.check_ports(template, {**spec.get("ports", {}), **ports}, checked, own)
        self.db.update_app(app_id, stack=json.dumps({**spec, "options": checked, "ports": published}))
        self.db.audit(actor, "stack-edit", True, service_id=app_id, detail=", ".join(sorted(options)) or "ports")
        return self.deployer.enqueue(app_id, "manual", actor)

    # -- deploying (Deployer hook) -------------------------------------------------------

    def deploy(self, app: dict, release: dict, rlog: ReleaseLog) -> None:
        import yaml  # pyyaml

        template, _ = self.template_of(app)
        ctx = self.ctx(app)
        folder = self.dir(app["id"])
        paths = app_paths(self.config, app["id"])
        website = bool(app["port"])
        first = not self.host.exists(f"{folder}/docker-compose.yml")
        rlog.step(f"{template.name}: {app['id']}")
        if app["domain"]:  # the address answers and has its certificate before the service starts
            rlog.step(f"Preparing {app['domain']}")
            page = "deploying" if first or not website else None
            self.deployer._install_vhost(app, ServiceType.NODE, paths, rlog, page)
            if not self.deployer._has_cert(app["domain"]) and self.deployer._ensure_certificate(app, rlog):
                self.deployer._install_vhost(app, ServiceType.NODE, paths, rlog, page)

        for d in (self.root, folder, ctx.files):
            self.host.makedirs(d)
        self.host.real(self.root).chmod(0o700)  # Docker mounts data as root: nobody else needs to look
        self.host.real(ctx.files).chmod(0o755)  # readable by images that do not run as root (nginx…)
        if template.owner:
            self.host.run(["chown", "-R", template.owner, ctx.data])
        content = (f"# Generated by Mas ({template.id}). Changes are overwritten on the next deploy.\n"
                   + yaml.safe_dump(template.compose(ctx), sort_keys=False))
        self.host.write_text(f"{folder}/docker-compose.yml", content, mode=0o600)
        try:
            rlog.step("Pulling the image")
            self.compose(app, ["pull"], rlog)
            rlog.step("Starting")
            self.compose(app, ["up", "-d", "--remove-orphans"], rlog)
            self._wait(app, rlog)
            if template.after:
                template.after(self, app, ctx, rlog)
        except StackError as exc:
            raise DeployError(str(exc)) from None
        if app["domain"]:
            rlog.step("nginx")
            if website:
                self.deployer._install_vhost(app, ServiceType.NODE, paths, rlog)
                self.deployer._health(app, ServiceType.NODE, folder, rlog)
            else:
                self.deployer._install_vhost(app, ServiceType.NODE, paths, rlog, page="service")
        if template.info:
            for row in template.info(ctx, self.host, server_ips(self.db, self.config)[0]).get("rows", []):
                if not row.get("secret"):
                    rlog.line(f"{row['label']}: {row['value']}")
        self.deployer._mark_live(app, release["id"], None)

    def _wait(self, app: dict, rlog: ReleaseLog, timeout: float = 180, healthy: bool = False) -> None:
        """Every container running; with ``healthy``, its health check passed too (a check still
        starting is fine otherwise: some images need their setup first)."""
        rlog.step("Waiting for the containers")
        deadline = time.monotonic() + timeout
        while True:
            res = self.host.run(self._cmd(app, ["ps", "--all", "--format", "json"]), timeout=30)
            states = []
            for line in (res.stdout.splitlines() if res is not None and res.ok else []):
                try:
                    items = json.loads(line)
                except ValueError:
                    continue
                for c in items if isinstance(items, list) else [items]:
                    states.append((c.get("Service") or c.get("Name"), c.get("State"), c.get("Health") or ""))
            ok = ("", "healthy") if healthy else ("", "healthy", "starting")
            if states and all(s == "running" and h in ok for _, s, h in states):
                rlog.line(", ".join(f"{n}: running" for n, _, _ in states))
                return
            dead = [n for n, s, _ in states if s in ("exited", "dead")]
            if dead:
                logs = self.compose(app, ["logs", "--tail", "40", "--no-color"])
                raise DeployError(f"{', '.join(dead)} stopped right after starting:\n{logs[-1500:]}")
            if time.monotonic() >= deadline:
                raise DeployError("the containers did not start in time: "
                                  + (", ".join(f"{n} {s} {h}".strip() for n, s, h in states) or "none"))
            time.sleep(3)

    def remove(self, app: dict, purge: bool) -> list[str]:
        done = []
        if self.host.exists(f"{self.dir(app['id'])}/docker-compose.yml"):
            try:
                self.compose(app, ["down", "--remove-orphans"], timeout=300)
                done.append("containers removed")
            except StackError as exc:
                done.append(f"warning: {exc}")
        if purge:
            self.host.remove(self.dir(app["id"]))
            done.append(f"removed {self.dir(app['id'])} (data included)")
        return done

    # -- dashboard -----------------------------------------------------------------------

    def info(self, app_id: str) -> dict:
        app = self.db.get_app(app_id)
        template, spec = self.template_of(app)
        details = template.info(self.ctx(app), self.host, server_ips(self.db, self.config)[0]) \
            if template.info else {}
        return {"template": template.public(), "options": spec.get("options", {}), "ports": spec.get("ports", {}),
                "folder": self.dir(app_id), "rows": [], "dns": [], "notes": [], **details}

    def control(self, app_id: str, op: str) -> str:
        if op not in ("start", "stop", "restart"):
            raise StackError("unknown action")
        return self.compose(self.db.get_app(app_id), [op], timeout=300)

    def logs(self, app_id: str, lines: int = 200) -> str:
        return self.compose(self.db.get_app(app_id), ["logs", "--tail", str(lines), "--no-color"], timeout=60)

    def action(self, app_id: str, action: str, values: dict) -> str:
        app = self.db.get_app(app_id)
        template, _ = self.template_of(app)
        if action not in template.actions:
            raise StackError("unknown action")
        return template.actions[action]["run"](self, app, self.ctx(app), values)


# -- dashboard texts ---------------------------------------------------------------------


def ui_texts() -> list[str]:
    """Every English text the catalog shows, for web/lib/stackTexts.ts (translations)."""
    out: list[str] = []
    for t in TEMPLATES.values():
        out += [t.name, t.description, t.domain_label]
        for f in t.fields + [f for a in t.actions.values() for f in a.get("fields", [])]:
            out += [f["label"], f.get("help", "")]
        out += [p.label for p in t.ports] + [a["label"] for a in t.actions.values()]
        if t.info:
            options = {f["key"]: f.get("default") or "x" for f in t.fields}
            sample = Ctx("x", options, {}, "/x", "/x", None, {p.name: p.container for p in t.ports}, "example.com")
            info = t.info(sample, _NoFiles(), None)
            out += [r["label"] for r in info.get("rows", [])] + [n["text"] for n in info.get("notes", [])]
    return list(dict.fromkeys(x for x in out if x))


class _NoFiles:
    """Host stand-in for ui_texts(): no file exists."""

    def is_dir(self, path: str) -> bool:
        return False

    def exists(self, path: str) -> bool:
        return False


if __name__ == "__main__":  # python -m mas.stacks > web/lib/stackTexts.ts
    print("// Generated by `python -m mas.stacks > web/lib/stackTexts.ts`: the texts of the service catalog,\n"
          "// sent by the API in English, listed here so they get translated. Do not edit by hand.\n"
          'import { msg } from "@/lib/i18n";\n\nexport const STACK_TEXTS = [')
    for text in ui_texts():
        print(f"  msg({json.dumps(text, ensure_ascii=False)}),")
    print("];")
