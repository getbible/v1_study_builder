#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Smoke-test a deployed GetBible study API origin.
#
#   verify_live_api.sh https://commentaries.getbible.net
#   verify_live_api.sh https://dictionaries.getbible.net
#
# Everything checked here is a promise the API makes to its clients: the paths
# resolve, the two cache tiers are right, precompressed variants are served,
# revalidation returns 304, CORS and the security headers are present, and
# failures come back as JSON rather than an HTML error page.
#
# Nothing is hardcoded to a module — the catalog is read and followed, so this
# works against any build. Exits non-zero on the first broken promise.
#
# Requires: bash 4, curl, python3.

set -uo pipefail

readonly PROGRAM="${0##*/}"
BASE="${1:-}"

if [[ -z "$BASE" || "$BASE" == -h || "$BASE" == --help ]]; then
    printf 'usage: %s https://commentaries.getbible.net [--insecure]\n' "$PROGRAM" >&2
    exit 2
fi
BASE="${BASE%/}"
shift || true

CURL=(curl --silent --show-error --location --max-time 30)
[[ "${1:-}" == "--insecure" ]] && CURL+=(--insecure)

for tool in curl python3; do
    command -v "$tool" >/dev/null || { printf '%s: %s is required\n' "$PROGRAM" "$tool" >&2; exit 2; }
done

pass=0
fail=0

check() {
    local label="$1" expected="$2" actual="$3"
    # HTTP/2 lowercases header names, so compare case-insensitively.
    if [[ "${actual,,}" == *"${expected,,}"* ]]; then
        printf '  ok    %s\n' "$label"
        pass=$((pass + 1))
    else
        printf '  FAIL  %-46s expected %-24s got: %s\n' "$label" "$expected" "$actual"
        fail=$((fail + 1))
    fi
}

body()    { "${CURL[@]}" "$1"; }
headers() { "${CURL[@]}" --output /dev/null --dump-header - "$1"; }
status()  { "${CURL[@]}" --output /dev/null --write-out '%{http_code}' "$@"; }
header()  { headers "$1" | grep -i "^$2:" | tr -d '\r' | tail -1; }

# Read one value out of a JSON document without needing jq on the server.
pick() {
    python3 -c '
import json, sys
document = json.loads(sys.stdin.read())
for key in sys.argv[1:]:
    document = document[int(key)] if isinstance(document, list) else document[key]
print(document)
' "$@"
}

echo "== $BASE =="

# ---------------------------------------------------------------------------
# Discover which resource this origin serves, then follow its own catalog.
# ---------------------------------------------------------------------------
RESOURCE=""
for candidate in commentaries dictionaries; do
    if [[ "$(status "$BASE/v1/$candidate.json")" == "200" ]]; then
        RESOURCE="$candidate"
        break
    fi
done
[[ -n "$RESOURCE" ]] || { printf '%s: no catalog at %s/v1/\n' "$PROGRAM" "$BASE" >&2; exit 1; }
echo "   serving: $RESOURCE"

CATALOG="$(body "$BASE/v1/$RESOURCE.json")"
MODULE="$(printf '%s' "$CATALOG" | pick "$RESOURCE" 0 id)" || exit 1
echo "   sampling module: $MODULE"

echo
echo "-- documents --"
check "catalog"          "200" "$(status "$BASE/v1/$RESOURCE.json")"
check "build stamp"      "200" "$(status "$BASE/v1/build.json")"
check "integrity manifest" "200" "$(status "$BASE/v1/hashes.json")"
check "module metadata"  "200" "$(status "$BASE/v1/$MODULE/metadata.json")"
check "bulk document"    "200" "$(status "$BASE/v1/$MODULE.json")"

if [[ "$RESOURCE" == commentaries ]]; then
    BOOKS="$(body "$BASE/v1/$MODULE/books.json")"
    BOOK="$(printf '%s' "$BOOKS" | pick books 0 book)"
    CHAPTER="$(printf '%s' "$BOOKS" | pick books 0 chapters 0)"
    HOT="$BASE/v1/$MODULE/$BOOK/$CHAPTER.json"
    check "books index"  "200" "$(status "$BASE/v1/$MODULE/books.json")"
    check "book document" "200" "$(status "$BASE/v1/$MODULE/$BOOK.json")"
    check "chapter document ($MODULE $BOOK:$CHAPTER)" "200" "$(status "$HOT")"
    check "schema published" "200" "$(status "$BASE/v1/schema/commentary-chapter.json")"
else
    INDEX="$(body "$BASE/v1/$MODULE/index.json")"
    ENTRY="$(printf '%s' "$INDEX" | pick entries 0 id)"
    HOT="$BASE/v1/$MODULE/$ENTRY.json"
    check "search index"  "200" "$(status "$BASE/v1/$MODULE/index.json")"
    check "word document ($MODULE/$ENTRY)" "200" "$(status "$HOT")"
    check "schema published" "200" "$(status "$BASE/v1/schema/dictionary-entry.json")"
fi

check "content type"     "application/json" "$(header "$HOT" content-type)"

echo
echo "-- caching --"
check "discovery is short-lived" "max-age=300"   "$(header "$BASE/v1/$RESOURCE.json" cache-control)"
check "content is long-lived"    "max-age=86400" "$(header "$HOT" cache-control)"
ETAG="$(header "$HOT" etag | cut -d' ' -f2)"
check "etag present"             '"'             "$ETAG"
check "revalidation returns 304" "304" \
    "$("${CURL[@]}" --output /dev/null --write-out '%{http_code}' --header "If-None-Match: $ETAG" "$HOT")"

echo
echo "-- compression --"
check "gzip variant served" "content-encoding: gzip" \
    "$("${CURL[@]}" --output /dev/null --dump-header - --header 'Accept-Encoding: gzip' "$HOT" \
        | grep -i '^content-encoding' | tr -d '\r')"
check "varies on encoding"  "accept-encoding" "$(header "$HOT" vary)"
plain=$("${CURL[@]}" --output /dev/null --write-out '%{size_download}' --header 'Accept-Encoding: identity' "$HOT")
small=$("${CURL[@]}" --output /dev/null --write-out '%{size_download}' --header 'Accept-Encoding: gzip, br' "$HOT")
if (( small < plain )); then
    printf '  ok    compressed transfer is smaller (%s -> %s bytes)\n' "$plain" "$small"
    pass=$((pass + 1))
else
    printf '  FAIL  compressed transfer is smaller (%s -> %s bytes)\n' "$plain" "$small"
    fail=$((fail + 1))
fi

echo
echo "-- CORS and security --"
HEAD="$(headers "$HOT")"
check "allows any origin"  "access-control-allow-origin: *" \
    "$(echo "$HEAD" | grep -i '^access-control-allow-origin' | tr -d '\r')"
check "exposes etag"       "etag" \
    "$(echo "$HEAD" | grep -i '^access-control-expose-headers' | tr -d '\r')"
check "nosniff"            "nosniff"           "$(echo "$HEAD" | grep -i '^x-content-type-options' | tr -d '\r')"
check "content policy"     "default-src 'none'" "$(echo "$HEAD" | grep -i '^content-security-policy' | tr -d '\r')"
check "resource policy"    "cross-origin"      "$(echo "$HEAD" | grep -i '^cross-origin-resource-policy' | tr -d '\r')"
check "strict transport"   "max-age="          "$(echo "$HEAD" | grep -i '^strict-transport-security' | tr -d '\r')"
check "preflight"          "204" "$(status --request OPTIONS "$HOT")"

if echo "$HEAD" | grep -qiE '^server: nginx/[0-9]'; then
    printf '  FAIL  server version is disclosed (set server_tokens off)\n'
    fail=$((fail + 1))
else
    printf '  ok    server version withheld\n'
    pass=$((pass + 1))
fi

echo
echo "-- failure modes --"
MISSING="$BASE/v1/$MODULE/does-not-exist-$$.json"
check "missing document 404" "404" "$(status "$MISSING")"
check "404 body is JSON"     '"error"' "$(body "$MISSING")"
check "404 content type"     "application/json" "$(header "$MISSING" content-type)"
check "writes rejected"      "405" "$(status --request POST "$HOT")"
check "repository metadata hidden" "404" "$(status "$BASE/.git/config")"

echo
printf '== %d passed, %d failed ==\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
