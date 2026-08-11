#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# End-to-end check of everything the Python tests cannot reach: that the shipped
# origin configuration installs onto a stock nginx, that deploy_static_api.sh
# produces a tree nginx can serve, and that both hosts keep every promise
# verify_live_api.sh asserts.
#
# It deliberately uses the distribution's own nginx.conf and the real installer
# rather than a purpose-built test config. A hand-rolled nginx.conf would not
# have caught, for example, that a stock nginx.conf already declares sendfile
# and gzip at http level and that redeclaring them is a fatal error.
#
# Needs root: it installs into /etc/nginx and serves on :443.
#
# Requires: nginx, git, rsync, python3, openssl, curl, and the builder installed.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="$(mktemp -d)"
HOSTS=(commentaries.getbible.net dictionaries.getbible.net)
HOSTS_MARKER="# getbible-api-check"

[[ "$EUID" -eq 0 ]] || { echo "must run as root (it installs into /etc/nginx)" >&2; exit 2; }
for tool in nginx git rsync python3 openssl curl; do
    command -v "$tool" >/dev/null || { echo "$tool is required" >&2; exit 2; }
done

cleanup() {
    nginx -s quit 2>/dev/null || true
    sed -i "/$HOSTS_MARKER/d" /etc/hosts 2>/dev/null || true
    rm -rf "$WORKSPACE"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Generate real API trees and publish each as a repository.
# ---------------------------------------------------------------------------
echo "== generating API trees =="
python3 "$REPO/tests/support/build_sample_tree.py" "$WORKSPACE/build"

for host in "${HOSTS[@]}"; do
    resource="${host%%.*}"
    repository="$WORKSPACE/repo-$resource"
    git init -q -b main "$repository"
    git -C "$repository" config user.name "nginx config check"
    git -C "$repository" config user.email "check@example.invalid"
    cp -a "$WORKSPACE/build/dist/$resource/v1" "$repository/v1"
    printf '# published %s\n' "$resource" > "$repository/README.md"
    git -C "$repository" add -A
    git -C "$repository" commit -qm "Sample $resource build"

    echo "== deploying $resource =="
    "$REPO/scripts/deploy_static_api.sh" \
        --repo "$repository" \
        --root "/var/www/getbible/$resource" \
        --work "$WORKSPACE/checkout-$resource" \
        --no-reload

    [[ -d "/var/www/getbible/$resource/v1" ]] || { echo "no v1 for $resource" >&2; exit 1; }
    if [[ -e "/var/www/getbible/$resource/README.md" ]]; then
        echo "deploy leaked a non-version file into the live root" >&2
        exit 1
    fi
done

# ---------------------------------------------------------------------------
# 2. Install the configuration exactly the way an operator would.
# ---------------------------------------------------------------------------
for host in "${HOSTS[@]}"; do
    install -d -m 755 "/etc/letsencrypt/live/$host"
    openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
        -keyout "/etc/letsencrypt/live/$host/privkey.pem" \
        -out "/etc/letsencrypt/live/$host/fullchain.pem" \
        -subj "/CN=$host" -addext "subjectAltName=DNS:$host" 2>/dev/null
    cp "/etc/letsencrypt/live/$host/fullchain.pem" "/etc/letsencrypt/live/$host/chain.pem"
    printf '127.0.0.1 %s %s\n' "$host" "$HOSTS_MARKER" >> /etc/hosts
done

# The stock default site binds :80 and :443 as default_server, which collides
# with nothing here — but it also binds [::] unconditionally, so it fails on a
# build host without IPv6. Drop it so the check tests this configuration only.
rm -f /etc/nginx/sites-enabled/default

echo "== installing =="
"$REPO/scripts/install_nginx_config.sh"

# ---------------------------------------------------------------------------
# 3. Serve both hosts and hold each to its own promises.
# ---------------------------------------------------------------------------
echo "== serving =="
# Start from empty logs so the assertion below sees only this run.
for host in "${HOSTS[@]}"; do
    : > "/var/log/nginx/$host.error.log"
    : > "/var/log/nginx/$host.access.log"
done
nginx
sleep 1

export no_proxy='*' NO_PROXY='*'
for host in "${HOSTS[@]}"; do
    echo
    "$REPO/scripts/verify_live_api.sh" "https://$host" --insecure
done

echo
echo "== error logs =="
found=0
for host in "${HOSTS[@]}"; do
    log="/var/log/nginx/$host.error.log"
    if [[ -f "$log" ]] && grep -Ei '\[(emerg|alert|crit|error)\]' "$log" | grep -v ssl_stapling; then
        found=1
    fi
done
if [[ "$found" -eq 1 ]]; then
    echo "nginx logged errors while serving" >&2
    exit 1
fi
echo "   clean"

# ---------------------------------------------------------------------------
# 4. The access log must be rotated by the stock logrotate glob, or it grows
#    without bound until it fills the disk.
# ---------------------------------------------------------------------------
if [[ -f /etc/logrotate.d/nginx ]]; then
    for host in "${HOSTS[@]}"; do
        access="/var/log/nginx/$host.access.log"
        [[ -f "$access" ]] || { echo "no access log for $host" >&2; exit 1; }
        if ! grep -q '/var/log/nginx/\*\.log' /etc/logrotate.d/nginx; then
            echo "   logrotate glob changed; re-check the access log name" >&2
        elif [[ "$access" != *.log ]]; then
            echo "access log $access is outside the logrotate glob" >&2
            exit 1
        fi
    done
    echo "== access logs are within the logrotate glob =="
fi
