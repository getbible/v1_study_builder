#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Stand the shipped origin configuration up over a real generated tree and run
# the live verifier against it.
#
# This covers the parts of the delivery that Python tests cannot reach: that
# docs/nginx/ parses, that deploy_static_api.sh produces a tree nginx can serve,
# and that the promises in verify_live_api.sh actually hold end to end.
#
# Requires: nginx, git, rsync, python3, openssl, curl, and the builder installed.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="$(mktemp -d)"
PORT="${NGINX_CHECK_PORT:-8443}"
NGINX_PREFIX="$WORKSPACE/nginx/conf"

cleanup() {
    if [[ -f "$WORKSPACE/nginx/logs/nginx.pid" ]]; then
        nginx -p "$NGINX_PREFIX/" -c "$NGINX_PREFIX/nginx.conf" -s quit 2>/dev/null || true
        sleep 1
    fi
    rm -rf "$WORKSPACE"
}
trap cleanup EXIT

for tool in nginx git rsync python3 openssl curl; do
    command -v "$tool" >/dev/null || { echo "$tool is required" >&2; exit 2; }
done

mkdir -p "$NGINX_PREFIX"/{conf.d,snippets,sites} "$WORKSPACE/nginx"/{logs,certs}

# ---------------------------------------------------------------------------
# 1. Generate a real API tree, publish it as a repository, and deploy it.
# ---------------------------------------------------------------------------
echo "== generating an API tree =="
python3 "$REPO/tests/support/build_sample_tree.py" "$WORKSPACE/build"

git -C "$WORKSPACE" init -q -b main sample
git -C "$WORKSPACE/sample" config user.name "nginx config check"
git -C "$WORKSPACE/sample" config user.email "check@example.invalid"
cp -a "$WORKSPACE/build/dist/commentaries/v1" "$WORKSPACE/sample/v1"
printf '# published commentaries\n' > "$WORKSPACE/sample/README.md"
git -C "$WORKSPACE/sample" add -A
git -C "$WORKSPACE/sample" commit -qm "Sample build"

echo "== deploying =="
"$REPO/scripts/deploy_static_api.sh" \
    --repo "$WORKSPACE/sample" \
    --root "$WORKSPACE/live" \
    --work "$WORKSPACE/checkout" \
    --no-reload

[[ -d "$WORKSPACE/live/v1" ]] || { echo "deploy produced no v1 directory" >&2; exit 1; }
if [[ -e "$WORKSPACE/live/README.md" ]]; then
    echo "deploy leaked a non-version file into the live root" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Adapt the shipped configuration to this host, changing as little as
#    possible so the check keeps testing what is actually shipped.
# ---------------------------------------------------------------------------
cp "$REPO/docs/nginx/getbible-api-http.conf" "$NGINX_PREFIX/conf.d/"
cp "$REPO/docs/nginx/snippets/"*.conf "$NGINX_PREFIX/snippets/"

HOST=commentaries.getbible.net
openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
    -keyout "$WORKSPACE/nginx/certs/key.pem" -out "$WORKSPACE/nginx/certs/cert.pem" \
    -subj "/CN=$HOST" -addext "subjectAltName=DNS:$HOST" 2>/dev/null

adapt=(-e "s|/var/www/getbible/commentaries|$WORKSPACE/live|"
       -e "s|/etc/letsencrypt/live/$HOST/fullchain.pem|$WORKSPACE/nginx/certs/cert.pem|"
       -e "s|/etc/letsencrypt/live/$HOST/privkey.pem|$WORKSPACE/nginx/certs/key.pem|"
       -e "s|/etc/letsencrypt/live/$HOST/chain.pem|$WORKSPACE/nginx/certs/cert.pem|"
       -e "s|/var/log/nginx/$HOST|$WORKSPACE/nginx/logs/$HOST|"
       -e 's|^    listen      80;$|    listen      '"$((PORT + 1))"';|')

# "http2 on" is nginx 1.25.1+. On older nginx, fold it back into the listen line.
version="$(nginx -v 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
if [[ "$(printf '%s\n1.25.1\n' "$version" | sort -V | head -1)" != "1.25.1" ]]; then
    echo "== nginx $version predates \"http2 on\"; folding it into listen =="
    adapt+=(-e 's|^    http2      on;$|    # http2 folded into listen for nginx < 1.25.1|'
            -e "s|^    listen     443 ssl;\$|    listen     $PORT ssl http2;|"
            -e "s|^    listen     \\[::\\]:443 ssl;\$|    listen     [::]:$PORT ssl http2;|")
else
    adapt+=(-e "s|^    listen     443 ssl;\$|    listen     $PORT ssl;|"
            -e "s|^    listen     \\[::\\]:443 ssl;\$|    listen     [::]:$PORT ssl;|")
fi

# Not every build host has IPv6.
if [[ ! -f /proc/net/if_inet6 ]]; then
    echo "== no IPv6 on this host; dropping the [::] listeners =="
    adapt+=(-e '/^    listen     \[::\]:/d' -e '/^    listen      \[::\]:/d')
fi

sed "${adapt[@]}" "$REPO/docs/nginx/$HOST.conf" > "$NGINX_PREFIX/sites/$HOST.conf"

# nginx workers must be able to read the workspace. As root they default to an
# unprivileged user that cannot; otherwise they inherit the invoking user.
privileged=""
[[ "$EUID" -eq 0 ]] && privileged="user root;"

cat > "$NGINX_PREFIX/nginx.conf" <<CONF
$privileged
worker_processes 2;
daemon on;
error_log $WORKSPACE/nginx/logs/error.log warn;
pid $WORKSPACE/nginx/logs/nginx.pid;
events { worker_connections 256; }
http {
    include $(ls /etc/nginx/mime.types >/dev/null 2>&1 && echo /etc/nginx/mime.types || echo mime.types);
    default_type application/octet-stream;
    client_body_temp_path $WORKSPACE/nginx/logs/body;
    proxy_temp_path       $WORKSPACE/nginx/logs/proxy;
    fastcgi_temp_path     $WORKSPACE/nginx/logs/fastcgi;
    uwsgi_temp_path       $WORKSPACE/nginx/logs/uwsgi;
    scgi_temp_path        $WORKSPACE/nginx/logs/scgi;
    include conf.d/getbible-api-http.conf;
    include sites/*.conf;
}
CONF

# ---------------------------------------------------------------------------
# 3. Parse it, serve it, and hold it to its own promises.
# ---------------------------------------------------------------------------
echo "== nginx -t =="
nginx -p "$NGINX_PREFIX/" -c "$NGINX_PREFIX/nginx.conf" -t

echo "== serving on $PORT =="
nginx -p "$NGINX_PREFIX/" -c "$NGINX_PREFIX/nginx.conf"
sleep 1

export no_proxy='*' NO_PROXY='*'
"$REPO/scripts/verify_live_api.sh" "https://127.0.0.1:$PORT" --insecure

echo "== error log =="
if grep -Ei '\[(emerg|alert|crit|error)\]' "$WORKSPACE/nginx/logs/error.log" | grep -v ssl_stapling; then
    echo "nginx logged errors while serving" >&2
    exit 1
fi
echo "   clean"
