#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Exercise deployment ref selection and failure isolation with local Git input.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="$(mktemp -d)"
trap 'rm -rf "$WORKSPACE"' EXIT

for tool in git rsync python3 gzip flock sha256sum; do
    command -v "$tool" >/dev/null || { echo "$tool is required" >&2; exit 2; }
done

SOURCE="$WORKSPACE/source"
git init -q -b main "$SOURCE"
git -C "$SOURCE" config user.name "deployment check"
git -C "$SOURCE" config user.email "check@example.invalid"
mkdir "$SOURCE/v1"
printf '{"schema":"deployment-fixture","text":"original document"}\n' > "$SOURCE/v1/sample.json"
digest="$(sha256sum "$SOURCE/v1/sample.json")"
printf '{"algorithm":"sha256","files":{"sample.json":"%s"}}\n' "${digest%% *}" \
    > "$SOURCE/v1/hashes.json"
git -C "$SOURCE" add -A
git -C "$SOURCE" commit -qm "Original documents"
git -C "$SOURCE" tag release-check

deploy() {
    "$REPO/scripts/deploy_static_api.sh" \
        --repo "$SOURCE" --root "$WORKSPACE/live" \
        --work "$WORKSPACE/checkout" --jobs 1 --no-reload "$@"
}

echo "== deploy and redeploy a tag =="
deploy --ref release-check
deploy --ref release-check
expected="$(git -C "$SOURCE" rev-parse release-check)"
[[ "$(cat "$WORKSPACE/live/.revision")" == "$expected" ]]
gzip -dc "$WORKSPACE/live/v1/sample.json.gz" | cmp - "$SOURCE/v1/sample.json"

echo "== switch to a branch outside the clone's fetch refspec =="
git -C "$SOURCE" checkout -qb deployment-check
printf 'branch marker\n' > "$SOURCE/README.md"
git -C "$SOURCE" add README.md
git -C "$SOURCE" commit -qm "Branch marker"
deploy --ref deployment-check
expected="$(git -C "$SOURCE" rev-parse deployment-check)"
[[ "$(cat "$WORKSPACE/live/.revision")" == "$expected" ]]

echo "== compressor failures leave the live tree untouched =="
mkdir "$WORKSPACE/failing-tools"
cat > "$WORKSPACE/failing-tools/gzip" <<'SH'
#!/usr/bin/env bash
printf 'incomplete compressed output'
exit 42
SH
chmod +x "$WORKSPACE/failing-tools/gzip"
if PATH="$WORKSPACE/failing-tools:$PATH" "$REPO/scripts/deploy_static_api.sh" \
    --repo "$SOURCE" --root "$WORKSPACE/live" \
    --work "$WORKSPACE/failed-checkout" --jobs 1 --no-reload; then
    echo "deploy accepted a failed compressor" >&2
    exit 1
fi
[[ "$(cat "$WORKSPACE/live/.revision")" == "$expected" ]]
gzip -dc "$WORKSPACE/live/v1/sample.json.gz" | cmp - "$SOURCE/v1/sample.json"
[[ ! -e "$WORKSPACE/failed-checkout/v1/sample.json.gz" ]]

echo "== unsafe manifest paths cannot escape the verified tree =="
mkdir "$WORKSPACE/outside"
cp "$SOURCE/v1/sample.json" "$WORKSPACE/outside/sample.json"
digest="$(sha256sum "$WORKSPACE/outside/sample.json")"
for unsafe in absolute symlink; do
    git -C "$SOURCE" checkout -qB "unsafe-$unsafe" main
    if [[ "$unsafe" == absolute ]]; then
        printf '{"algorithm":"sha256","files":{"sample.json":"%s","%s":"%s"}}\n' \
            "${digest%% *}" "$WORKSPACE/outside/sample.json" "${digest%% *}" \
            > "$SOURCE/v1/hashes.json"
    else
        ln -s "$WORKSPACE/outside" "$SOURCE/v1/external"
        printf '{"algorithm":"sha256","files":{"sample.json":"%s","external/sample.json":"%s"}}\n' \
            "${digest%% *}" "${digest%% *}" > "$SOURCE/v1/hashes.json"
    fi
    git -C "$SOURCE" add -A
    git -C "$SOURCE" commit -qm "Unsafe $unsafe manifest fixture"
    if "$REPO/scripts/deploy_static_api.sh" \
        --repo "$SOURCE" --ref "unsafe-$unsafe" --root "$WORKSPACE/live" \
        --work "$WORKSPACE/checkout-$unsafe" --jobs 1 --no-reload; then
        echo "deploy accepted an unsafe $unsafe manifest path" >&2
        exit 1
    fi
    [[ "$(cat "$WORKSPACE/live/.revision")" == "$expected" ]]
done

echo "== deployment checks passed =="
