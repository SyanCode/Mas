#!/usr/bin/env bash
# Mas on a real Debian: the system Python, a real nginx with hand-written vhosts, and the unit tests.
#
#   docker run --rm -v "$PWD":/src:ro debian:12 bash /src/tests/debian/run.sh
#   docker run --rm -v "$PWD":/src:ro debian:13 bash /src/tests/debian/run.sh
#
# Runs as root in a throwaway container (it installs packages and writes /etc/nginx). Set MAS_SRC to
# the source tree when it is not /src.
set -euo pipefail

SRC=${MAS_SRC:-/src}
WORK=$(mktemp -d)
export DEBIAN_FRONTEND=noninteractive

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mFAIL:\033[0m %s\n' "$*" >&2; exit 1; }

[ -f /etc/debian_version ] || die "this check is for Debian"
log "Debian $(cat /etc/debian_version), $(python3 -V)"

log "Packages: python3-venv, nginx"
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 python3-venv python3-pip nginx git ca-certificates >/dev/null

log "Installing Mas in a venv from a copy of the sources (the mount is read-only)"
mkdir "$WORK/src"
tar -C "$SRC" --exclude=.git --exclude=node_modules --exclude=.next -cf - . | tar -C "$WORK/src" -xf -
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q -e "$WORK/src[dev]"
MAS="$WORK/venv/bin/mas"
"$MAS" --version >/dev/null

log "An old-fashioned server: three vhosts written by hand and a Node-like app on a port"
mkdir -p /var/www/blog /var/www/shop /etc/nginx/sites-enabled /etc/nginx/sites-available
echo "<h1>blog</h1>" > /var/www/blog/index.php
echo "<h1>shop</h1>" > /var/www/shop/index.html
rm -f /etc/nginx/sites-enabled/default
cat > /etc/nginx/sites-available/legacy.conf <<'CONF'
server {
    listen 80;
    server_name blog.example.com;
    root /var/www/blog;
    index index.php;
    location ~ \.php$ { fastcgi_pass unix:/run/php/php-fpm.sock; include fastcgi_params; }
}
server {
    listen 80;
    server_name shop.example.com;
    root /var/www/shop;
}
server {
    listen 80;
    server_name api.example.com;
    location / { proxy_pass http://127.0.0.1:3999; }
}
CONF
ln -sf /etc/nginx/sites-available/legacy.conf /etc/nginx/sites-enabled/legacy.conf
nginx -t
(cd "$WORK" && python3 -m http.server 3999 --bind 127.0.0.1 >/dev/null 2>&1 &)
sleep 1

log "mas scan: every site is found, nothing is touched"
before=$(find /etc/nginx -type f -exec sha256sum {} + | sort)
"$MAS" scan --json > "$WORK/scan.json"
after=$(find /etc/nginx -type f -exec sha256sum {} + | sort)
[ "$before" = "$after" ] || die "mas scan changed the nginx configuration"
python3 - "$WORK/scan.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
services = data["services"] if isinstance(data, dict) else data
domains = {d: s for s in services for d in s.get("domains", [])}
for name in ("blog.example.com", "shop.example.com", "api.example.com"):
    assert name in domains, f"{name} not detected: {sorted(domains)}"
assert domains["blog.example.com"]["type"] == "php", domains["blog.example.com"]["type"]
assert domains["shop.example.com"]["type"] == "static", domains["shop.example.com"]["type"]
assert domains["api.example.com"]["port"] == 3999, domains["api.example.com"].get("port")
print(f"{len(services)} services, the three sites are detected")
PY

log "Preview vhost: the generated nginx file is valid, serves an allowed address and refuses the others"
mkdir -p /srv/mas/previews/current
echo "preview ok" > /srv/mas/previews/current/index.html
"$WORK/venv/bin/python" - <<'PY'
from pathlib import Path
from mas import templates
body = templates.restrict(templates.static_body("/srv/mas/previews/current"), ["127.0.0.1", "203.0.113.7"])
text = templates.vhost("prev-demo", "pr1.demo.example.com", body, False).replace("    listen [::]:80;\n", "")
Path("/etc/nginx/sites-enabled/prev-demo.conf").write_text(text)
PY
nginx -t
nginx
sleep 1
code() { curl -s -o /dev/null -w "%{http_code}" --interface "$1" -H "Host: pr1.demo.example.com" "http://127.0.0.1$2"; }
[ "$(code 127.0.0.1 /)" = 200 ] || die "an allowed address was refused"
[ "$(code 127.0.0.2 /)" = 403 ] || die "an address that is not allowed got through"
[ "$(code 127.0.0.1 /.git/config)" = 403 ] || die "dot files must stay refused for everyone"
curl -s -I --interface 127.0.0.2 -H "Host: pr1.demo.example.com" http://127.0.0.1/ | grep -qi "x-robots-tag: noindex" || die "no noindex header"
nginx -s stop

log "mas doctor: the health checks run on this nginx without failing (no systemd in a container, so no reload test)"
"$MAS" doctor --no-dns >/dev/null || "$MAS" doctor >/dev/null || die "mas doctor failed"

log "The unit tests with this Python (the browser ones need Chromium and run in the main CI)"
cd "$WORK/src"
"$WORK/venv/bin/python" -m pytest -q -x --ignore=tests/e2e --ignore=tests/test_cms_ui.py --ignore=tests/test_browser_audit.py

log "OK on Debian $(cat /etc/debian_version)"
