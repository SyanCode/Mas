"""Build a fake Debian + nginx server tree for tests and demos.

    python tests/fixtures/server.py /tmp/mas-demo
    mas scan --fixture /tmp/mas-demo

What lives on the fake server:

- app.example.com    Next.js app run by PM2 (user alice), HTTPS, certbot 80->443 redirect,
                     /api/ routed to the Python API through an upstream block
- api.example.com    Python (gunicorn) systemd unit api.service
- blog.example.com   PHP-FPM site (+ www alias redirect), git repo, cert expiring soon
- docs.example.com   static site with two server_name values (doc.example.com)
- grafana.example.com  Docker container behind an upstream
- shop.example.com   proxy to a port nobody listens on (app down)
- nginx default site, node_exporter, mariadb, a PM2 worker, a stopped container and an
  ad-hoc python http.server without vhost (all "no domain")
"""

from __future__ import annotations

import hashlib
import json
import sys
import textwrap
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

APP_CERT_EXPIRES = datetime(2027, 1, 15, 12, 0, tzinfo=timezone.utc)
BLOG_CERT_DAYS_LEFT = 9
GRAFANA_ID = "a1b2c3d4e5f6" + "0" * 52
REDIS_ID = "f6e5d4c3b2a1" + "1" * 52


def _write(base: Path, rel: str, content: str | bytes) -> Path:
    path = base / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8", newline="\n")
    return path


def _cert(common_name: str, expires: datetime) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(expires - timedelta(days=90))
        .not_valid_after(expires)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def _git_repo(root: Path, path: str, remote: str, branch: str, subject: str, when: int) -> str:
    body = (
        "tree 4b825dc642cb6eb9a060e54bf8d69288fbee4904\n"
        f"author Alice Martin <alice@example.com> {when} +0200\n"
        f"committer Alice Martin <alice@example.com> {when} +0200\n\n"
        f"{subject}\n"
    ).encode()
    obj = b"commit %d\0" % len(body) + body
    sha = hashlib.sha1(obj).hexdigest()
    git = f"{path}/.git"
    _write(root, f"{git}/HEAD", f"ref: refs/heads/{branch}\n")
    _write(root, f"{git}/config", f"""
        [core]
        \trepositoryformatversion = 0
        \tbare = false
        [remote "origin"]
        \turl = {remote}
        \tfetch = +refs/heads/*:refs/remotes/origin/*
        [branch "{branch}"]
        \tremote = origin
        \tmerge = refs/heads/{branch}
        """)
    _write(root, f"{git}/refs/heads/{branch}", sha + "\n")
    _write(root, f"{git}/objects/{sha[:2]}/{sha[2:]}", zlib.compress(obj))
    return sha


def _proc(root: Path, pid: int, *, name: str, argv: list[str], cwd: str, uid: int,
          ppid: int = 1, cgroup: str = "/", env: dict[str, str] | None = None) -> None:
    base = f"/proc/{pid}"
    _write(root, f"{base}/status", f"Name:\t{name}\nState:\tS (sleeping)\nPid:\t{pid}\n"
                                   f"PPid:\t{ppid}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    _write(root, f"{base}/cmdline", b"\0".join(a.encode() for a in argv) + b"\0")
    _write(root, f"{base}/cwd", cwd)  # FixtureHost: plain file = symlink target
    _write(root, f"{base}/cgroup", f"0::{cgroup}\n")
    _write(root, f"{base}/environ", b"".join(f"{k}={v}".encode() + b"\0" for k, v in (env or {}).items()))


def build(dest: Path, *, docker: bool = True) -> Path:
    dest = Path(dest)
    root = dest / "root"
    now = datetime.now(timezone.utc)

    # -- users -------------------------------------------------------------
    _write(root, "/etc/passwd", """
        root:x:0:0:root:/root:/bin/bash
        www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin
        mysql:x:106:112:MariaDB Server,,,:/nonexistent:/bin/false
        api:x:998:998::/srv/api:/usr/sbin/nologin
        prometheus:x:997:997::/var/lib/prometheus:/usr/sbin/nologin
        alice:x:1000:1000:Alice,,,:/home/alice:/bin/bash
        """)

    # -- nginx -------------------------------------------------------------
    _write(root, "/etc/nginx/nginx.conf", """
        user www-data;
        worker_processes auto;
        events { worker_connections 768; }
        http {
            include /etc/nginx/mime.types;
            include /etc/nginx/conf.d/*.conf;
            include /etc/nginx/sites-enabled/*;
        }
        """)
    _write(root, "/etc/nginx/mime.types", "types { text/html html; }\n")
    _write(root, "/etc/nginx/proxy_params", """
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        """)
    _write(root, "/etc/nginx/snippets/fastcgi-php.conf", r"""
        fastcgi_split_path_info ^(.+?\.php)(/.*)$;
        try_files $fastcgi_script_name =404;
        set $path_info $fastcgi_path_info;
        fastcgi_param PATH_INFO $path_info;
        fastcgi_index index.php;
        include fastcgi.conf;
        """)
    _write(root, "/etc/nginx/fastcgi.conf", "fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;\n")
    _write(root, "/etc/letsencrypt/options-ssl-nginx.conf", """
        ssl_session_cache shared:le_nginx_SSL:10m;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256";
        """)

    _write(root, "/etc/nginx/sites-enabled/default", """
        # Default server configuration
        server {
            listen 80 default_server;
            listen [::]:80 default_server;
            root /var/www/html;
            index index.html index.htm index.nginx-debian.html;
            server_name _;
            location / {
                try_files $uri $uri/ =404;
            }
        }
        """)
    _write(root, "/etc/nginx/sites-enabled/app.example.com", """
        server {
            server_name app.example.com;

            location / {
                proxy_pass http://127.0.0.1:3000;
                include proxy_params;
            }

            location /api/ {
                proxy_pass http://api_backend;
                include proxy_params;
            }

            listen 443 ssl; # managed by Certbot
            ssl_certificate /etc/letsencrypt/live/app.example.com/fullchain.pem; # managed by Certbot
            ssl_certificate_key /etc/letsencrypt/live/app.example.com/privkey.pem; # managed by Certbot
            include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
        }

        server {
            if ($host = app.example.com) {
                return 301 https://$host$request_uri;
            } # managed by Certbot

            listen 80;
            server_name app.example.com;
            return 404; # managed by Certbot
        }
        """)
    _write(root, "/etc/nginx/sites-enabled/api", """
        server {
            listen 80;
            server_name api.example.com;
            location / {
                proxy_pass http://localhost:8000/;
                include proxy_params;
            }
        }
        """)
    _write(root, "/etc/nginx/sites-enabled/blog", r"""
        server {
            listen 80;
            listen 443 ssl http2;
            server_name blog.example.com;
            root /var/www/blog/public;
            index index.php;

            ssl_certificate /etc/letsencrypt/live/blog.example.com/fullchain.pem;
            ssl_certificate_key /etc/letsencrypt/live/blog.example.com/privkey.pem;

            if ($scheme != "https") {
                return 301 https://$host$request_uri;
            }

            location / {
                try_files $uri $uri/ /index.php?$query_string;
            }

            location ~ \.php$ {
                include snippets/fastcgi-php.conf;
                fastcgi_pass unix:/run/php/php8.2-fpm.sock;
            }

            location ~ /\.(?!well-known).* {
                deny all;
            }
        }

        server {
            listen 80;
            server_name www.blog.example.com;
            return 301 https://blog.example.com$request_uri;
        }
        """)
    _write(root, "/etc/nginx/sites-enabled/grafana", """
        upstream grafana {
            server 127.0.0.1:3001;
            keepalive 8;
        }

        server {
            listen 80;
            server_name grafana.example.com;
            location / {
                proxy_pass http://grafana;
                proxy_set_header Host $host;
            }
        }
        """)
    _write(root, "/etc/nginx/sites-enabled/shop", """
        server {
            listen 80;
            server_name shop.example.com;
            location / { proxy_pass http://127.0.0.1:4000; }
        }
        """)
    _write(root, "/etc/nginx/conf.d/upstreams.conf", """
        upstream api_backend {
            server 127.0.0.1:8000 max_fails=3;
        }
        """)
    _write(root, "/etc/nginx/conf.d/docs.conf", """
        server {
            listen 80;
            server_name docs.example.com doc.example.com;
            root /var/www/docs;
            index index.html;
        }
        """)

    _write(root, "/etc/letsencrypt/live/app.example.com/fullchain.pem", _cert("app.example.com", APP_CERT_EXPIRES))
    blog_expires = (now + timedelta(days=BLOG_CERT_DAYS_LEFT, hours=12)).replace(microsecond=0)
    _write(root, "/etc/letsencrypt/live/blog.example.com/fullchain.pem", _cert("blog.example.com", blog_expires))

    # -- site files --------------------------------------------------------
    _write(root, "/var/www/html/index.nginx-debian.html", "<h1>Welcome to nginx!</h1>\n")
    _write(root, "/var/www/docs/index.html", "<h1>Docs</h1>\n")
    _write(root, "/var/www/blog/public/index.php", "<?php echo 'blog';\n")
    _write(root, "/var/www/blog/composer.json", "{}\n")
    _git_repo(root, "/var/www/blog", "https://deploy:ghp_secret@github.com/acme/blog.git", "main",
              "Fix pagination on archive pages", 1760000000)
    _write(root, "/home/alice/apps/web/package.json", json.dumps(
        {"name": "web", "scripts": {"start": "next start"},
         "dependencies": {"next": "15.0.0", "react": "19.0.0"}}, indent=2))
    _git_repo(root, "/home/alice/apps/web", "git@github.com:acme/web.git", "production",
              "Add pricing page", 1760900000)
    _write(root, "/home/alice/apps/worker/package.json", json.dumps({"name": "worker"}, indent=2))
    _write(root, "/home/alice/apps/worker/worker.js", "setInterval(() => {}, 1000);\n")
    _write(root, "/srv/api/requirements.txt", "fastapi\ngunicorn\nuvicorn\n")
    _git_repo(root, "/srv/api", "https://github.com/acme/api.git", "main", "Bump dependencies", 1760500000)
    _write(root, "/home/alice/scratch/notes.txt", "tmp\n")
    _write(root, "/opt/monitoring/docker-compose.yml", "services:\n  grafana:\n    image: grafana/grafana\n")

    # -- processes ---------------------------------------------------------
    _proc(root, 1999, name="PM2 v5.4.2: God", argv=["PM2 v5.4.2: God Daemon (/home/alice/.pm2)"],
          cwd="/home/alice", uid=1000, cgroup="/system.slice/pm2-alice.service")
    _proc(root, 2000, name="node", argv=["node", "/home/alice/apps/web/node_modules/.bin/next", "start"],
          cwd="/home/alice/apps/web", uid=1000, ppid=1999, cgroup="/system.slice/pm2-alice.service",
          env={"pm_id": "0", "name": "web", "PM2_HOME": "/home/alice/.pm2", "PORT": "3000"})
    _proc(root, 2001, name="next-server (v1", argv=["next-server (v15.0.0)"],
          cwd="/home/alice/apps/web", uid=1000, ppid=2000, cgroup="/system.slice/pm2-alice.service")
    _proc(root, 2100, name="node", argv=["node", "/home/alice/apps/worker/worker.js"],
          cwd="/home/alice/apps/worker", uid=1000, ppid=1999, cgroup="/system.slice/pm2-alice.service",
          env={"pm_id": "1", "name": "worker"})
    _proc(root, 3000, name="gunicorn", argv=["/srv/api/.venv/bin/python3", "/srv/api/.venv/bin/gunicorn",
          "-k", "uvicorn.workers.UvicornWorker", "-b", "127.0.0.1:8000", "app.main:app"],
          cwd="/srv/api", uid=998, cgroup="/system.slice/api.service")
    _proc(root, 3001, name="gunicorn", argv=["/srv/api/.venv/bin/python3", "/srv/api/.venv/bin/gunicorn",
          "-k", "uvicorn.workers.UvicornWorker", "-b", "127.0.0.1:8000", "app.main:app"],
          cwd="/srv/api", uid=998, ppid=3000, cgroup="/system.slice/api.service")
    _proc(root, 4001, name="prometheus-node", argv=["/usr/bin/prometheus-node-exporter"],
          cwd="/", uid=997, cgroup="/system.slice/prometheus-node-exporter.service")
    _proc(root, 5001, name="python3", argv=["python3", "-m", "http.server", "5055"],
          cwd="/home/alice/scratch", uid=1000, ppid=4999,
          cgroup="/user.slice/user-1000.slice/session-3.scope")
    _proc(root, 4999, name="bash", argv=["-bash"], cwd="/home/alice", uid=1000,
          cgroup="/user.slice/user-1000.slice/session-3.scope")
    _proc(root, 800, name="mariadbd", argv=["/usr/sbin/mariadbd"], cwd="/var/lib/mysql", uid=106,
          cgroup="/system.slice/mariadb.service")
    _proc(root, 900, name="php-fpm8.2", argv=["php-fpm: master process (/etc/php/8.2/fpm/php-fpm.conf)"],
          cwd="/", uid=0, cgroup="/system.slice/php8.2-fpm.service")
    _write(root, "/run/php/php8.2-fpm.sock", "")

    # -- pm2 ---------------------------------------------------------------
    _write(root, "/home/alice/.pm2/pm2.pid", "1999\n")
    _write(root, "/usr/local/bin/pm2", "#!/usr/bin/env node\n")

    # -- docker ------------------------------------------------------------
    if docker:
        _write(root, "/var/run/docker.sock", "")
        _write(dest, "docker/containers.json", json.dumps([
            {"Id": GRAFANA_ID, "Names": ["/monitoring-grafana-1"], "Image": "grafana/grafana:11.2.0",
             "State": "running", "Status": "Up 3 days",
             "Ports": [{"IP": "0.0.0.0", "PrivatePort": 3000, "PublicPort": 3001, "Type": "tcp"},
                       {"IP": "::", "PrivatePort": 3000, "PublicPort": 3001, "Type": "tcp"}],
             "Labels": {"com.docker.compose.project": "monitoring",
                        "com.docker.compose.project.working_dir": "/opt/monitoring",
                        "com.docker.compose.service": "grafana"}},
            {"Id": REDIS_ID, "Names": ["/old-redis"], "Image": "redis:7", "State": "exited",
             "Status": "Exited (0) 2 weeks ago", "Ports": [{"PrivatePort": 6379, "Type": "tcp"}],
             "Labels": {}},
        ], indent=2))
        _write(dest, "docker.json", json.dumps({"/containers/json?all=1": "docker/containers.json"}))

    # -- commands ----------------------------------------------------------
    _write(dest, "commands/ss-tcp.txt", """
        LISTEN 0      511          0.0.0.0:80         0.0.0.0:*    users:(("nginx",pid=1101,fd=6),("nginx",pid=1100,fd=6))
        LISTEN 0      511          0.0.0.0:443        0.0.0.0:*    users:(("nginx",pid=1101,fd=8),("nginx",pid=1100,fd=8))
        LISTEN 0      511             [::]:80            [::]:*    users:(("nginx",pid=1101,fd=7),("nginx",pid=1100,fd=7))
        LISTEN 0      128          0.0.0.0:22         0.0.0.0:*    users:(("sshd",pid=700,fd=3))
        LISTEN 0      128             [::]:22            [::]:*    users:(("sshd",pid=700,fd=4))
        LISTEN 0      4096   127.0.0.53%lo:53         0.0.0.0:*    users:(("systemd-resolve",pid=500,fd=14))
        LISTEN 0      511        127.0.0.1:3000       0.0.0.0:*    users:(("next-server (v1",pid=2001,fd=21))
        LISTEN 0      2048       127.0.0.1:8000       0.0.0.0:*    users:(("gunicorn",pid=3001,fd=5),("gunicorn",pid=3000,fd=5))
        LISTEN 0      4096         0.0.0.0:3001       0.0.0.0:*    users:(("docker-proxy",pid=4100,fd=4))
        LISTEN 0      4096            [::]:3001          [::]:*    users:(("docker-proxy",pid=4101,fd=4))
        LISTEN 0      4096               *:9100             *:*    users:(("prometheus-node",pid=4001,fd=3))
        LISTEN 0      5            0.0.0.0:5055       0.0.0.0:*    users:(("python3",pid=5001,fd=3))
        LISTEN 0      80         127.0.0.1:3306       0.0.0.0:*    users:(("mariadbd",pid=800,fd=20))
        """)
    _write(dest, "commands/ss-unix.txt", """
        u_str LISTEN 0      511    /run/php/php8.2-fpm.sock 21345            * 0     users:(("php-fpm8.2",pid=901,fd=10),("php-fpm8.2",pid=900,fd=8))
        u_str LISTEN 0      4096   /run/docker.sock 18234            * 0     users:(("systemd",pid=1,fd=40))
        """)
    units = [
        ("api.service", "Acme API (gunicorn)"),
        ("cron.service", "Regular background program processing daemon"),
        ("dbus.service", "D-Bus System Message Bus"),
        ("docker.service", "Docker Application Container Engine"),
        ("mariadb.service", "MariaDB 10.11.6 database server"),
        ("nginx.service", "A high performance web server and a reverse proxy server"),
        ("php8.2-fpm.service", "The PHP 8.2 FastCGI Process Manager"),
        ("pm2-alice.service", "PM2 process manager"),
        ("prometheus-node-exporter.service", "Prometheus exporter for machine metrics"),
        ("ssh.service", "OpenBSD Secure Shell server"),
        ("systemd-journald.service", "Journal Service"),
    ]
    _write(dest, "commands/systemctl-list.json", json.dumps(
        [{"unit": u, "load": "loaded", "active": "active", "sub": "running", "description": d} for u, d in units]))
    _write(dest, "commands/systemctl-show.txt", """
        Id=api.service
        Description=Acme API (gunicorn)
        MainPID=3000
        WorkingDirectory=/srv/api
        User=api
        ExecStart={ path=/srv/api/.venv/bin/gunicorn ; argv[]=/srv/api/.venv/bin/gunicorn -k uvicorn.workers.UvicornWorker -b 127.0.0.1:8000 app.main:app ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
        FragmentPath=/etc/systemd/system/api.service
        ActiveState=active
        SubState=running

        Id=mariadb.service
        Description=MariaDB 10.11.6 database server
        MainPID=800
        WorkingDirectory=
        User=mysql
        ExecStart={ path=/usr/sbin/mariadbd ; argv[]=/usr/sbin/mariadbd $MYSQLD_OPTS ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
        FragmentPath=/lib/systemd/system/mariadb.service
        ActiveState=active
        SubState=running

        Id=prometheus-node-exporter.service
        Description=Prometheus exporter for machine metrics
        MainPID=4001
        WorkingDirectory=
        User=prometheus
        ExecStart={ path=/usr/bin/prometheus-node-exporter ; argv[]=/usr/bin/prometheus-node-exporter ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
        FragmentPath=/lib/systemd/system/prometheus-node-exporter.service
        ActiveState=active
        SubState=running
        """)
    _write(dest, "commands/pm2-alice.json", json.dumps([
        {"pid": 2000, "name": "web", "pm_id": 0,
         "pm2_env": {"status": "online", "pm_cwd": "/home/alice/apps/web", "username": "alice",
                     "pm_exec_path": "/home/alice/apps/web/node_modules/.bin/next", "PORT": "3000",
                     "DATABASE_URL": "postgres://secret@localhost/web"}},
        {"pid": 2100, "name": "worker", "pm_id": 1,
         "pm2_env": {"status": "online", "pm_cwd": "/home/alice/apps/worker",
                     "pm_exec_path": "/home/alice/apps/worker/worker.js"}},
        {"pid": 0, "name": "report", "pm_id": 2,
         "pm2_env": {"status": "stopped", "pm_cwd": "/home/alice/apps/worker",
                     "pm_exec_path": "/home/alice/apps/worker/report.js"}},
    ]))
    _write(dest, "commands.json", json.dumps({
        "ss -tlnpH": "commands/ss-tcp.txt",
        "ss -xlnpH": "commands/ss-unix.txt",
        "systemctl list-units * --output=json": "commands/systemctl-list.json",
        "systemctl show *": "commands/systemctl-show.txt",
        "runuser -u alice -- env PM2_HOME=/home/alice/.pm2 * jlist": "commands/pm2-alice.json",
    }, indent=2))
    return dest


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: server.py <destination>")
    print(build(Path(sys.argv[1])))
