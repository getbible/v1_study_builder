#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Deploy one generated GetBible study API onto a static origin.
#
#   deploy_static_api.sh --repo git@github.com:getbible/commentaries.git \
#                        --root /var/www/getbible/commentaries
#
# The deploy is a pull, a verify, a compress, and a sync — in that order, and it
# stops at the first failure, so a bad build never reaches the live root.
#
# Two properties of the generated API make this safe and cheap, and the script
# is built around them:
#
#   1. Output is byte-stable. A module that did not change rebuilds identically,
#      so `git reset --hard` rewrites only genuinely changed files and every
#      other file keeps its mtime. nginx derives ETags from mtime and size, so
#      unchanged documents keep their ETag across a deploy and clients keep
#      getting 304 instead of re-downloading the corpus every month.
#
#   2. hashes.json lists a SHA-256 for every other document in the tree. It is
#      both the integrity manifest and the list of paths the builder owns, so it
#      is what this script verifies before anything goes live.
#
# Requires: bash 4, git, rsync, python3, gzip. Uses brotli when present.

set -euo pipefail

REPO=""
REF="main"
ROOT=""
WORK=""
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
RELOAD=1
DRY_RUN=0
REQUIRE_SIGNATURE=0
VERIFY_URL=""

readonly PROGRAM="${0##*/}"

die() {
    printf '%s: %s\n' "$PROGRAM" "$*" >&2
    exit 1
}

log() {
    printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

usage() {
    sed -n '3,28p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'USAGE'

Options:
  --repo URL            Source repository (required unless --work already exists)
  --ref NAME            Branch or tag to deploy (default: main)
  --root DIR            Live document root nginx serves (required)
  --work DIR            Persistent checkout (default: /var/lib/getbible/<root name>)
  --jobs N              Parallel compression jobs (default: CPU count)
  --require-signature   Refuse to deploy a commit without a valid GPG signature
  --verify-url URL      After reload, assert this URL returns 200 and JSON
  --no-reload           Do not reload nginx
  --dry-run             Show what would change without touching the live root
  -h, --help            This message
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="${2:?--repo needs a value}"; shift 2 ;;
        --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
        --root) ROOT="${2:?--root needs a value}"; shift 2 ;;
        --work) WORK="${2:?--work needs a value}"; shift 2 ;;
        --jobs) JOBS="${2:?--jobs needs a value}"; shift 2 ;;
        --verify-url) VERIFY_URL="${2:?--verify-url needs a value}"; shift 2 ;;
        --require-signature) REQUIRE_SIGNATURE=1; shift ;;
        --no-reload) RELOAD=0; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

[[ -n "$ROOT" ]] || die "--root is required"
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || die "--jobs must be a positive integer"
[[ -n "$WORK" ]] || WORK="/var/lib/getbible/$(basename "$ROOT")"

for tool in git rsync python3 gzip; do
    command -v "$tool" >/dev/null || die "$tool is required but not installed"
done

# ---------------------------------------------------------------------------
# Only one deploy per root at a time.
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$WORK")"
LOCK="${WORK}.lock"
exec 9>"$LOCK"
flock -n 9 || die "another deploy is already running for $ROOT"

# ---------------------------------------------------------------------------
# 1. Pull. Only changed files are rewritten, so mtimes and ETags survive.
# ---------------------------------------------------------------------------
if [[ ! -d "$WORK/.git" ]]; then
    [[ -n "$REPO" ]] || die "$WORK is not a checkout and no --repo was given"
    log "cloning $REPO into $WORK"
    git clone --branch "$REF" --single-branch "$REPO" "$WORK"
else
    if [[ -n "$REPO" ]]; then
        actual="$(git -C "$WORK" remote get-url origin)"
        [[ "$actual" == "$REPO" ]] || die "$WORK points at $actual, expected $REPO"
    fi
    log "fetching $REF"
    git -C "$WORK" fetch --prune origin "$REF"
    git -C "$WORK" reset --hard "origin/$REF"
    # Drop anything untracked except the compressed variants, which this script
    # owns and prunes itself. Cleaning those away would force a full brotli pass
    # over the whole corpus on every deploy.
    git -C "$WORK" clean -fdx -e '*.json.gz' -e '*.json.br'
fi

REVISION="$(git -C "$WORK" rev-parse HEAD)"
log "deploying $REVISION"

if [[ "$REQUIRE_SIGNATURE" -eq 1 ]]; then
    git -C "$WORK" verify-commit HEAD \
        || die "commit $REVISION has no valid signature and --require-signature was given"
    log "signature verified"
fi

# ---------------------------------------------------------------------------
# 2. Verify. hashes.json must describe the tree exactly before it goes live.
# ---------------------------------------------------------------------------
verify_tree() {
    python3 - "$1" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest_path = root / "hashes.json"
if not manifest_path.is_file():
    sys.exit(f"no hashes.json under {root}")

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("algorithm") != "sha256":
    sys.exit(f"unsupported digest algorithm: {manifest.get('algorithm')!r}")

files = manifest.get("files") or {}
if not files:
    sys.exit("hashes.json lists no files")

problems = []
for relative, expected in sorted(files.items()):
    path = root / relative
    if ".." in Path(relative).parts or path.is_symlink():
        problems.append(f"unsafe manifest path: {relative}")
        continue
    if not path.is_file():
        problems.append(f"missing: {relative}")
        continue
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        problems.append(f"digest mismatch: {relative}")

published = {
    path.relative_to(root).as_posix()
    for path in root.rglob("*.json")
    if path.name != "hashes.json"
}
for extra in sorted(published - set(files)):
    problems.append(f"not in manifest: {extra}")

if problems:
    for problem in problems[:20]:
        print(problem, file=sys.stderr)
    remaining = len(problems) - 20
    if remaining > 0:
        print(f"... and {remaining} more", file=sys.stderr)
    sys.exit(f"{len(problems)} integrity problem(s) under {root}")

print(f"verified {len(files)} documents")
PY
}

shopt -s nullglob
VERSIONS=("$WORK"/v[0-9]*/)
shopt -u nullglob
[[ ${#VERSIONS[@]} -gt 0 ]] || die "$WORK contains no version directory"

for version in "${VERSIONS[@]}"; do
    log "verifying ${version#"$WORK"/}"
    verify_tree "$version"
done

# ---------------------------------------------------------------------------
# 3. Compress. Only for documents whose variant is missing or stale, and the
#    variant carries the document's mtime so its ETag is stable too.
# ---------------------------------------------------------------------------
compress_one() {
    local file="$1"
    if [[ ! -f "$file.gz" || "$file" -nt "$file.gz" ]]; then
        gzip -9 -c -- "$file" > "$file.gz.tmp"
        mv -f -- "$file.gz.tmp" "$file.gz"
        touch -r "$file" -- "$file.gz"
    fi
    if command -v brotli >/dev/null && [[ ! -f "$file.br" || "$file" -nt "$file.br" ]]; then
        brotli -q 11 -c -- "$file" > "$file.br.tmp"
        mv -f -- "$file.br.tmp" "$file.br"
        touch -r "$file" -- "$file.br"
    fi
}
export -f compress_one

log "compressing with $JOBS job(s)"
# shellcheck disable=SC2016  # $1 is the child shell's argument, not this one's
find "$WORK" -path "$WORK/.git" -prune -o -type f -name '*.json' -print0 \
    | xargs -0 -r -P "$JOBS" -I{} bash -c 'compress_one "$1"' _ {}

# Drop variants whose document no longer exists, so a removed module cannot
# leave a stale compressed copy that nginx would still serve.
pruned=0
while IFS= read -r -d '' variant; do
    if [[ ! -f "${variant%.*}" ]]; then
        rm -f -- "$variant"
        pruned=$((pruned + 1))
    fi
done < <(find "$WORK" -path "$WORK/.git" -prune -o -type f \( -name '*.json.gz' -o -name '*.json.br' \) -print0)
[[ "$pruned" -eq 0 ]] || log "pruned $pruned orphaned compressed file(s)"

# ---------------------------------------------------------------------------
# 4. Sync into the live root, one version directory at a time. Scoping --delete
#    to a single version means no failure mode of this script can remove
#    anything outside a version directory, and the live root ends up holding
#    only what is actually served — no repository metadata, no README.
# ---------------------------------------------------------------------------
RSYNC_FLAGS=(
    --archive
    --delete --delete-delay --delay-updates
    "--chmod=D755,F644"
    --omit-dir-times
    --human-readable
    --exclude='*.tmp'
)

if [[ "$DRY_RUN" -eq 1 ]]; then
    log "dry run — changes that would be applied to $ROOT:"
    for version in "${VERSIONS[@]}"; do
        rsync "${RSYNC_FLAGS[@]}" --dry-run --itemize-changes \
            "$version" "$ROOT/$(basename "$version")/"
    done
    exit 0
fi

mkdir -p "$ROOT"
for version in "${VERSIONS[@]}"; do
    name="$(basename "$version")"
    log "syncing $name into $ROOT"
    mkdir -p "$ROOT/$name"
    rsync "${RSYNC_FLAGS[@]}" "$version" "$ROOT/$name/"
done

# Retire a version directory that the publication repository no longer carries.
for existing in "$ROOT"/v[0-9]*/; do
    [[ -d "$existing" ]] || continue
    name="$(basename "$existing")"
    if [[ ! -d "$WORK/$name" ]]; then
        log "removing retired version $name"
        rm -rf -- "${ROOT:?}/$name"
    fi
done

for version in "${VERSIONS[@]}"; do
    log "verifying live $(basename "$version")"
    verify_tree "$ROOT/$(basename "$version")"
done

printf '%s\n' "$REVISION" > "$ROOT/.revision"

# ---------------------------------------------------------------------------
# 5. Reload. New workers start with an empty open-file cache, which is what
#    makes the newly deployed documents visible immediately.
# ---------------------------------------------------------------------------
if [[ "$RELOAD" -eq 1 ]]; then
    if command -v nginx >/dev/null; then
        nginx -t
        if command -v systemctl >/dev/null && systemctl is-active --quiet nginx; then
            systemctl reload nginx
        else
            nginx -s reload
        fi
        log "nginx reloaded"
    else
        log "nginx not found; skipping reload"
    fi
fi

if [[ -n "$VERIFY_URL" ]]; then
    command -v curl >/dev/null || die "--verify-url needs curl"
    log "verifying $VERIFY_URL"
    # No --fail: the status code is what is being checked, so it has to come
    # back rather than turning into a bare non-zero exit with no explanation.
    response="$(curl --silent --show-error --location --max-time 30 \
        --output /dev/null --write-out '%{http_code} %{content_type}' "$VERIFY_URL" || true)"
    status="${response%% *}"
    content_type="${response#* }"
    [[ -n "$status" && "$status" != "000" ]] || die "could not reach $VERIFY_URL"
    [[ "$status" == "200" ]] || die "$VERIFY_URL returned HTTP $status"
    [[ "$content_type" == application/json* ]] \
        || die "$VERIFY_URL returned Content-Type ${content_type:-none}"
    log "live check passed ($status $content_type)"
fi

log "deployed $REVISION to $ROOT"
