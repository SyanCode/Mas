#!/usr/bin/env bash
# Install or upgrade Mas (idempotent) on Debian/Ubuntu, Fedora, RHEL/Rocky/AlmaLinux 9+, Arch or openSUSE,
# with systemd and nginx.
#
#   sudo ./deploy/install.sh                       # asks for the dashboard domain
#   sudo ./deploy/install.sh --domain mas.example.com --email you@example.com
#   sudo ./deploy/install.sh --no-nginx            # API on 127.0.0.1:7070 only (SSH tunnel)
#   sudo ./deploy/install.sh --admin syan          # name of the first account (asked otherwise)
#
# Layout: /opt/mas/{src,venv}  /etc/mas/config.toml  /var/lib/mas/mas.db
set -euo pipefail

MAS_HOME=/opt/mas
SRC=$MAS_HOME/src
VENV=$MAS_HOME/venv
ETC=/etc/mas
DATA=/var/lib/mas
NGINX_CONF=/etc/nginx/sites-available/mas.conf   # Debian layout; set again below for the other distributions
NGINX_LINK=/etc/nginx/sites-enabled/mas.conf
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
STAMP=$(date +%Y%m%d%H%M%S)

DOMAIN=""
EMAIL=""
ADMIN=""
WITH_NGINX=1

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --domain) DOMAIN=${2:-}; shift 2 ;;
        --email) EMAIL=${2:-}; shift 2 ;;
        --admin) ADMIN=${2:-}; shift 2 ;;
        --no-nginx) WITH_NGINX=0; shift ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

# -- checks -------------------------------------------------------------------
if [ -n "$EMAIL" ] && ! [[ "$EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
    die "invalid email: $EMAIL"
fi
[ "$(id -u)" -eq 0 ] || die "run as root: sudo $0"
[ -f "$REPO/pyproject.toml" ] && [ -d "$REPO/mas" ] || die "run this script from the Mas source tree"
[ -d /run/systemd/system ] || die "Mas needs systemd (Alpine/OpenRC, WSL without systemd… are not supported)"

# -- distribution ---------------------------------------------------------------
# shellcheck disable=SC1091
. /etc/os-release
FAMILY=""
for id in $ID ${ID_LIKE:-}; do
    case "$id" in
        debian|ubuntu) FAMILY=debian; break ;;
        fedora|rhel|centos|rocky|almalinux|ol) FAMILY=rhel; break ;;
        arch|manjaro|endeavouros) FAMILY=arch; break ;;
        opensuse*|suse|sles) FAMILY=suse; break ;;
    esac
done
[ -n "$FAMILY" ] || die "unsupported distribution: ${PRETTY_NAME:-$ID} (Debian/Ubuntu, Fedora, RHEL/Rocky/Alma, Arch or openSUSE)"
log "Distribution: ${PRETTY_NAME:-$ID} ($FAMILY)"

# -- packages -----------------------------------------------------------------
log "Installing system packages"
NEED=""
command -v npm >/dev/null || NEED="$NEED nodejs npm"
command -v nginx >/dev/null || NEED="$NEED nginx"
case "$FAMILY" in
    debian)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        # shellcheck disable=SC2086
        apt-get install -y -qq --no-install-recommends python3-venv git certbot python3-certbot-nginx $NEED >/dev/null ;;
    rhel)
        if [ "$ID" != fedora ]; then  # certbot comes from EPEL on RHEL and its rebuilds
            dnf install -y -q epel-release >/dev/null 2>&1 || dnf install -y -q \
                "https://dl.fedoraproject.org/pub/epel/epel-release-latest-${VERSION_ID%%.*}.noarch.rpm" >/dev/null
        fi
        # shellcheck disable=SC2086
        dnf install -y -q git certbot python3-certbot-nginx tar $NEED >/dev/null
        python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || dnf install -y -q python3.12 >/dev/null \
            || dnf install -y -q python3.11 >/dev/null ;;
    arch)
        # shellcheck disable=SC2086
        pacman -Sy --needed --noconfirm python git certbot certbot-nginx $NEED >/dev/null ;;
    suse)
        # shellcheck disable=SC2086
        zypper --non-interactive --quiet install -y python3 git certbot python3-certbot-nginx $NEED >/dev/null
        python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || zypper --non-interactive --quiet \
            install -y python312 >/dev/null || zypper --non-interactive --quiet install -y python311 >/dev/null ;;
esac
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
        PYTHON=$(command -v "$candidate"); break
    fi
done
[ -n "$PYTHON" ] || die "Python 3.11+ required and none could be installed (found $(python3 -V 2>&1))"

# -- nginx layout -----------------------------------------------------------------
# Debian/Ubuntu: sites-available + sites-enabled. Elsewhere: conf.d, included from the http block of nginx.conf
# (Arch does not include it by default). Mas writes its sites the same way (mas.deploy.detect_layout).
if [ ! -d /etc/nginx/sites-enabled ]; then
    install -d -m 755 /etc/nginx/conf.d
    NGINX_CONF=/etc/nginx/conf.d/mas.conf
    NGINX_LINK=""
    if ! grep -Eq '^[[:space:]]*include[[:space:]]+(/etc/nginx/)?conf\.d/\*\.conf' /etc/nginx/nginx.conf; then
        log "Including /etc/nginx/conf.d/*.conf in nginx.conf"
        cp /etc/nginx/nginx.conf "/etc/nginx/nginx.conf.mas-bak-$STAMP"
        sed -i '0,/^[[:space:]]*http[[:space:]]*{/s//&\n    include \/etc\/nginx\/conf.d\/*.conf;/' /etc/nginx/nginx.conf
    fi
fi
systemctl enable --now --quiet nginx 2>/dev/null || true

# SELinux (Fedora, RHEL…): nginx may then talk to the apps on 127.0.0.1 and serve the files under /srv/mas.
if command -v selinuxenabled >/dev/null && selinuxenabled; then
    log "SELinux: allowing nginx to reach the apps and read /srv/mas"
    setsebool -P httpd_can_network_connect 1 || warn "setsebool failed: nginx may not reach the apps"
    if command -v semanage >/dev/null; then
        semanage fcontext -a -t httpd_sys_content_t '/srv/mas(/.*)?' 2>/dev/null || true
    fi
fi
# firewalld (Fedora, RHEL, openSUSE): open HTTP and HTTPS.
if command -v firewall-cmd >/dev/null && systemctl is-active --quiet firewalld; then
    firewall-cmd --quiet --permanent --add-service=http --add-service=https && firewall-cmd --quiet --reload || true
fi

# -- source -------------------------------------------------------------------
if [ -f "$MAS_HOME/pyvenv.cfg" ]; then
    # Manual phase 1 install (venv directly in /opt/mas): replace it.
    log "Removing the previous manual install in $MAS_HOME"
    rm -rf "$MAS_HOME"
fi
install -d -m 755 "$MAS_HOME"

if [ "$REPO" != "$SRC" ]; then
    log "Copying source to $SRC"
    if [ -d "$SRC" ]; then
        mv "$SRC" "$SRC.mas-bak-$STAMP"
        # keep the 2 most recent backups
        ls -1d "$SRC".mas-bak-* 2>/dev/null | sort | head -n -2 | xargs -r rm -rf
    fi
    install -d -m 755 "$SRC"
    tar -C "$REPO" --exclude=./.venv --exclude=./web/node_modules --exclude=./web/.next \
        --exclude=./web/out --exclude='*.tar.gz' -cf - . | tar -C "$SRC" -xf -
fi

log "Installing Python package in $VENV"
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet --upgrade "$SRC"
ln -sf "$VENV/bin/mas" /usr/local/bin/mas

log "Building the dashboard"
(cd "$SRC/web" && npm ci --no-audit --no-fund --loglevel=error && NEXT_TELEMETRY_DISABLED=1 npm run build >/dev/null)

# -- config & data ------------------------------------------------------------
install -d -m 700 "$ETC" "$DATA"
install -d -m 755 /srv/mas
if command -v restorecon >/dev/null && command -v selinuxenabled >/dev/null && selinuxenabled; then
    restorecon -R /srv/mas 2>/dev/null || true
fi
if [ ! -f "$ETC/config.toml" ]; then
    log "Writing $ETC/config.toml"
    install -m 600 /dev/null "$ETC/config.toml"
    cat > "$ETC/config.toml" <<'EOF'
# Mas configuration. Restart after changes: systemctl restart mas

[server]
port = 7070
session_hours = 12
scan_interval = 60

[scan]
# Units to hide in addition to the built-in list (ssh, cron, nginx, systemd-*…)
extra_exclude_units = []
# Hide systemd units with no port and only system paths in their command
hide_os_daemons = true

[deploy]
# Apps deployed from GitHub: /srv/mas/apps/<app>, ports 7100-7999, 5 releases kept
keep_releases = 5
EOF
fi
if [ -n "$EMAIL" ] && ! grep -q '^letsencrypt_email' "$ETC/config.toml"; then
    if grep -q '^\[deploy\]' "$ETC/config.toml"; then
        sed -i "/^\[deploy\]/a letsencrypt_email = \"$EMAIL\"" "$ETC/config.toml"
    else
        printf '\n[deploy]\nletsencrypt_email = "%s"\n' "$EMAIL" >> "$ETC/config.toml"
    fi
fi

# -- admin account (before the service, so the dashboard is never open) ------
if [ -z "$(mas user list 2>/dev/null)" ]; then
    log "Creating the admin account"
    [ -t 0 ] || die "no admin account yet: run 'sudo mas user create <name>' interactively"
    while [ -z "$ADMIN" ]; do
        read -r -p "Admin username [admin]: " ADMIN
        ADMIN=${ADMIN:-admin}
        ADMIN=${ADMIN,,}
        if ! [[ "$ADMIN" =~ ^[a-z][a-z0-9_.-]{1,31}$ ]]; then
            warn "2-32 characters: lowercase letters, digits, . _ - (starting with a letter)"
            ADMIN=""
        fi
    done
    mas user create "$ADMIN"
fi

# -- systemd ------------------------------------------------------------------
log "Installing mas.service"
if [ -f /etc/systemd/system/mas.service ] && ! cmp -s "$SRC/deploy/mas.service" /etc/systemd/system/mas.service; then
    cp /etc/systemd/system/mas.service "/etc/systemd/system/mas.service.mas-bak-$STAMP"
fi
install -m 644 "$SRC/deploy/mas.service" /etc/systemd/system/mas.service
systemctl daemon-reload
systemctl enable --quiet mas
systemctl restart mas

# -- nginx + certificate ------------------------------------------------------
if [ "$WITH_NGINX" -eq 1 ]; then
    if [ -z "$DOMAIN" ] && [ -t 0 ]; then
        read -r -p "Dashboard domain (e.g. mas.example.com, empty to skip): " DOMAIN
    fi
fi

if [ "$WITH_NGINX" -eq 1 ] && [ -n "$DOMAIN" ]; then
    [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "invalid domain: $DOMAIN"

    if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
        log "Requesting a certificate for $DOMAIN (DNS must point to this server)"
        if [ -z "$EMAIL" ] && [ -t 0 ]; then
            read -r -p "Email for Let's Encrypt expiry notices (empty = none): " EMAIL
        fi
        if [ -n "$EMAIL" ]; then CONTACT=(-m "$EMAIL"); else CONTACT=(--register-unsafely-without-email); fi
        # certbot.timer may be renewing other certificates right now: wait for it.
        for _ in $(seq 1 60); do
            pgrep -x certbot >/dev/null || break
            [ "$_" -eq 1 ] && log "Another certbot is running (renewal?), waiting for it to finish"
            sleep 5
        done
        certbot certonly --nginx --non-interactive --agree-tos "${CONTACT[@]}" \
            --cert-name "$DOMAIN" -d "$DOMAIN" \
            || die "certbot failed (see its message above). Mas is installed; fix the cause, then re-run this script"
    fi

    log "Installing nginx vhost $NGINX_CONF"
    BACKUP=""
    if [ -f "$NGINX_CONF" ]; then
        BACKUP="$NGINX_CONF.mas-bak-$STAMP"
        cp "$NGINX_CONF" "$BACKUP"
    fi
    sed "s/__DOMAIN__/$DOMAIN/g" "$SRC/deploy/nginx-mas.conf" > "$NGINX_CONF.tmp"
    mv "$NGINX_CONF.tmp" "$NGINX_CONF"
    [ -z "$NGINX_LINK" ] || ln -sf "$NGINX_CONF" "$NGINX_LINK"

    if ! nginx -t 2>/tmp/mas-nginx-test.log; then
        cat /tmp/mas-nginx-test.log >&2
        if [ -n "$BACKUP" ]; then mv "$BACKUP" "$NGINX_CONF"; else rm -f "$NGINX_CONF" ${NGINX_LINK:+"$NGINX_LINK"}; fi
        die "nginx -t failed, previous configuration restored, nginx NOT reloaded"
    fi
    systemctl reload nginx
    URL="https://$DOMAIN"
else
    URL=""
fi

# -- done ---------------------------------------------------------------------
sleep 1
systemctl is-active --quiet mas || warn "mas.service is not running: journalctl -u mas -n 50"
mas --version 2>/dev/null || true
log "Mas is installed"
if [ -n "$URL" ]; then
    echo "    Dashboard: $URL"
else
    echo "    Dashboard (no domain): from your PC run"
    echo "      ssh -L 7070:127.0.0.1:7070 $(logname 2>/dev/null || echo user)@$(hostname -I | awk '{print $1}')"
    echo "    then open http://localhost:7070"
fi
echo "    CLI: mas --help   (mas scan, mas logs <service>, mas restart <service>, mas history…)"
