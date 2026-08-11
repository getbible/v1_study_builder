#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Install the GetBible study API origin configuration, adapted to this host.
#
#   install_nginx_config.sh --root-dir /var/www/getbible
#   install_nginx_config.sh --host commentaries.getbible.net --reload
#
# The shipped configuration in docs/nginx/ targets current nginx. Three things
# legitimately differ between hosts, and guessing wrong on any of them makes
# nginx refuse to start, so they are detected rather than assumed:
#
#   * "http2 on" is nginx 1.25.1+. Older nginx needs it folded into listen.
#   * brotli_static needs ngx_brotli, which many distributions do not package.
#   * IPv6 listeners fail outright on a host without IPv6.
#
# Nothing is written until every adapted file has passed nginx -t.
#
# Requires: bash 4, nginx.

set -euo pipefail

readonly PROGRAM="${0##*/}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../docs/nginx" && pwd)"
readonly SOURCE_DIR

PREFIX="/etc/nginx"
ROOT_DIR="/var/www/getbible"
HOSTS=()
RELOAD=0
DRY_RUN=0

die() { printf '%s: %s\n' "$PROGRAM" "$*" >&2; exit 1; }
log() { printf '  %s\n' "$*"; }

usage() {
    cat <<'USAGE'
usage: install_nginx_config.sh [options]

  --host NAME       Install only this host (repeatable; default: both)
  --prefix DIR      nginx configuration prefix (default: /etc/nginx)
  --root-dir DIR    Parent of the live document roots (default: /var/www/getbible)
  --reload          Reload nginx after a successful install
  --dry-run         Show what would be installed, write nothing
  -h, --help        This message
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOSTS+=("${2:?--host needs a value}"); shift 2 ;;
        --prefix) PREFIX="${2:?--prefix needs a value}"; shift 2 ;;
        --root-dir) ROOT_DIR="${2:?--root-dir needs a value}"; shift 2 ;;
        --reload) RELOAD=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

command -v nginx >/dev/null || die "nginx is not installed"
[[ ${#HOSTS[@]} -gt 0 ]] || HOSTS=(commentaries.getbible.net dictionaries.getbible.net)

for host in "${HOSTS[@]}"; do
    [[ -f "$SOURCE_DIR/$host.conf" ]] || die "no shipped configuration for $host"
done

# nginx refuses to start when a referenced certificate is missing, and the
# resulting error names a file rather than the thing to do about it. Check first
# and say the actual next step.
if [[ "$DRY_RUN" -eq 0 ]]; then
    missing=()
    for host in "${HOSTS[@]}"; do
        [[ -f "/etc/letsencrypt/live/$host/fullchain.pem" ]] || missing+=("$host")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        printf '%s: no certificate for: %s\n\n' "$PROGRAM" "${missing[*]}" >&2
        printf 'Issue them first. The stock nginx site already serves the ACME\n' >&2
        printf 'webroot on port 80, so this works before anything here is installed:\n\n' >&2
        printf '  certbot certonly --webroot -w /var/www/html%s\n\n' \
            "$(printf ' \\\n      -d %s' "${missing[@]}")" >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Detect what this host actually supports.
# ---------------------------------------------------------------------------
VERSION="$(nginx -v 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)"
[[ -n "$VERSION" ]] || die "could not determine the nginx version"

MODULES="$(nginx -V 2>&1)"
adapt=()

echo "Detected:"
log "nginx $VERSION"

if [[ "$(printf '%s\n1.25.1\n' "$VERSION" | sort -V | head -1)" != "1.25.1" ]]; then
    log "HTTP/2: folding \"http2 on\" into listen (needs 1.25.1+)"
    adapt+=(-e 's|^    http2      on;$|    # http2 folded into listen below (nginx < 1.25.1)|'
            -e 's|^    listen     443 ssl;$|    listen     443 ssl http2;|'
            -e 's|^    listen     \[::\]:443 ssl;$|    listen     [::]:443 ssl http2;|')
else
    log "HTTP/2: native \"http2 on\""
fi

snippet_adapt=()
if [[ "$MODULES" == *brotli* ]] || ls /usr/lib/nginx/modules/ngx_http_brotli_static_module.so >/dev/null 2>&1; then
    log "Brotli: available, enabling brotli_static"
    snippet_adapt+=(-e 's|^\( *\)# brotli_static on;$|\1brotli_static on;|')
else
    log "Brotli: not available, serving gzip only (roughly 15-20% larger on JSON)"
fi

if [[ ! -f /proc/net/if_inet6 ]]; then
    log "IPv6: unavailable, commenting out the [::] listeners"
    adapt+=(-e 's|^\( *\)listen \(.*\)\[::\]\(.*\)$|\1# listen \2[::]\3|')
else
    log "IPv6: available"
fi

if [[ "$ROOT_DIR" != "/var/www/getbible" ]]; then
    log "Document roots: $ROOT_DIR"
    adapt+=(-e "s|/var/www/getbible/|${ROOT_DIR%/}/|")
fi

# ---------------------------------------------------------------------------
# Build the adapted set in a staging directory and test it before installing.
# ---------------------------------------------------------------------------
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/conf.d" "$STAGE/snippets" "$STAGE/sites-available"

cp "$SOURCE_DIR/getbible-api-http.conf" "$STAGE/conf.d/"
for snippet in "$SOURCE_DIR"/snippets/*.conf; do
    if [[ ${#snippet_adapt[@]} -gt 0 ]]; then
        sed "${snippet_adapt[@]}" "$snippet" > "$STAGE/snippets/$(basename "$snippet")"
    else
        cp "$snippet" "$STAGE/snippets/"
    fi
done
for host in "${HOSTS[@]}"; do
    if [[ ${#adapt[@]} -gt 0 ]]; then
        sed "${adapt[@]}" "$SOURCE_DIR/$host.conf" > "$STAGE/sites-available/$host.conf"
    else
        cp "$SOURCE_DIR/$host.conf" "$STAGE/sites-available/$host.conf"
    fi
done

echo
echo "Would install into $PREFIX:"
for file in "$STAGE"/conf.d/* "$STAGE"/snippets/* "$STAGE"/sites-available/*; do
    log "${file#"$STAGE"/}"
done

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo
    echo "Dry run; nothing written. Adapted files were left in:"
    trap - EXIT
    echo "  $STAGE"
    exit 0
fi

[[ -w "$PREFIX" ]] || die "$PREFIX is not writable; run as root"

echo
echo "Installing:"
install -d -m 755 "$PREFIX/conf.d" "$PREFIX/snippets" "$PREFIX/sites-available" "$PREFIX/sites-enabled"
install -m 644 "$STAGE/conf.d/getbible-api-http.conf" "$PREFIX/conf.d/"
install -m 644 "$STAGE"/snippets/*.conf "$PREFIX/snippets/"
for host in "${HOSTS[@]}"; do
    install -m 644 "$STAGE/sites-available/$host.conf" "$PREFIX/sites-available/"
    ln -sfn "$PREFIX/sites-available/$host.conf" "$PREFIX/sites-enabled/$host.conf"
    install -d -m 755 "${ROOT_DIR%/}/${host%%.*}"
    log "$host -> ${ROOT_DIR%/}/${host%%.*}"
done

echo
echo "Testing:"
if ! nginx -t; then
    die "nginx rejected the installed configuration; it has NOT been reloaded"
fi

if [[ "$RELOAD" -eq 1 ]]; then
    if command -v systemctl >/dev/null && systemctl is-active --quiet nginx; then
        systemctl reload nginx
    else
        nginx -s reload
    fi
    echo "  reloaded"
else
    echo "  not reloaded; run 'systemctl reload nginx' when ready"
fi
