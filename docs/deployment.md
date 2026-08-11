# Deploying the study APIs

Two origins, each serving one generated repository as static JSON. There is no
application process, no database, and no request-time logic — a request is a
file read, which is why the whole design leans on the filesystem and the CDN
rather than on code.

| Origin | Serves | Live root |
| --- | --- | --- |
| `commentaries.getbible.net` | `getbible/commentaries` | `/var/www/getbible/commentaries` |
| `dictionaries.getbible.net` | `getbible/dictionaries` | `/var/www/getbible/dictionaries` |

Everything below applies identically to both. Where a command names one, run the
same command with the other's repository and root.

## What the builder guarantees

The deploy depends on four properties. If you change the builder, keep them, or
the deployment stops being safe:

1. **Output is byte-stable.** A module that has not changed rebuilds to an
   identical file. Content documents carry no timestamp — only `build.json` and
   the catalog do — so an unchanged corpus produces an unchanged tree.
2. **`v1/hashes.json` describes the whole tree.** It holds a SHA-256 for every
   other document, so it is both the integrity manifest and the list of paths
   the builder owns.
3. **The builder owns only version directories.** Anything else in the
   publication repository — README, licence, workflows — is the repository's
   own and is never served.
4. **Every document is plain text JSON.** No HTML is published anywhere, which
   is why the origin can send `Content-Security-Policy: default-src 'none'` and
   why no consumer has to sanitize a response.

## Server layout

```
/var/lib/getbible/commentaries      persistent Git checkout (working copy)
/var/www/getbible/commentaries      live root nginx serves — version dirs only
/etc/nginx/conf.d/getbible-api-http.conf        zones and log format (http only)
/etc/nginx/snippets/getbible-api-server.conf    tuning, TLS, compression
/etc/nginx/snippets/getbible-api-headers.conf   CORS and security headers
/etc/nginx/snippets/getbible-api-v1.conf        the v1 locations
/etc/nginx/sites-available/commentaries.getbible.net.conf
```

The live root is **not** a Git checkout. The checkout stays in `/var/lib`, and
only the version directories are copied across. That keeps repository metadata
off the origin entirely rather than relying on a rule to hide it.

## First-time setup

Requires nginx (1.25.1+ preferred; older is adapted automatically), git,
rsync, python3, gzip, and ideally brotli.

```bash
apt-get install -y nginx git rsync python3 certbot brotli
# Brotli for nginx is a separate module; without it only .gz is served, which
# still works and is roughly 15-20% larger on JSON.
apt-get install -y libnginx-mod-http-brotli   # where packaged

install -d -m 755 /var/lib/getbible
```

Issue the certificates first — nginx refuses to start when a referenced
certificate is missing. The stock nginx site already serves `/var/www/html` on
port 80, so this works before anything below is installed, and the installed
configuration keeps serving the same webroot afterwards so renewals need no
further changes:

```bash
certbot certonly --webroot -w /var/www/html \
    -d commentaries.getbible.net -d dictionaries.getbible.net
```

Then install the origin configuration:

```bash
scripts/install_nginx_config.sh --reload
```

Do not copy `docs/nginx/` into place by hand. Three things legitimately differ
between hosts, and guessing wrong on any of them stops nginx from starting, so
the installer detects them instead:

- **`http2 on` is nginx 1.25.1+.** On older nginx — Ubuntu 24.04 LTS still ships
  1.24 — it is folded back into the `listen` line.
- **`brotli_static` needs `ngx_brotli`**, which many distributions do not
  package. It is enabled only when the module is actually present.
- **IPv6 listeners fail outright** on a host without IPv6.

The installer stages the adapted files, refuses to proceed if a certificate is
missing (naming the exact certbot command), and runs `nginx -t` before
reloading. `--dry-run` shows what it would install and leaves the adapted files
for inspection.

Reload nginx automatically after each renewal:

```bash
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'HOOK'
#!/bin/sh
systemctl reload nginx
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

## Deploying

```bash
scripts/deploy_static_api.sh \
    --repo git@github.com:getbible/commentaries.git \
    --root /var/www/getbible/commentaries \
    --require-signature \
    --verify-url https://commentaries.getbible.net/v1/commentaries.json
```

The script does five things, and the order is the point — nothing reaches the
live root until it has been proven good:

1. **Pull.** `git reset --hard` into the persistent checkout. Git rewrites only
   files whose content changed, so every unchanged document keeps its mtime.
2. **Verify.** Every digest in `hashes.json` is recomputed, and any `.json` in
   the tree that the manifest does not list is an error. A build that fails here
   never reaches the live root; the previous deploy keeps serving.
3. **Compress.** `.gz` and `.br` are written beside each document, but only
   where missing or stale, and each variant is stamped with its document's
   mtime. A monthly rebuild that changes two modules recompresses two modules.
4. **Sync.** `rsync --delete --delay-updates`, one version directory at a time.
   Scoping the delete to a single version means no failure of this script can
   remove anything outside a version directory.
5. **Reload.** `nginx -t` then reload. New workers start with an empty
   open-file cache, which is what makes the new documents visible at once.

`--require-signature` refuses any commit without a valid GPG signature. The
build workflow signs its commits when the publication secrets are present, so on
a production origin this should always be on: it means the origin serves only
what the build key signed.

It verifies against the deploying user's own GPG keyring, so import and trust
the build key once on each origin before enabling it — otherwise every deploy
fails closed with an unsigned-commit error:

```bash
sudo -u deploy gpg --import getbible-build-key.asc
sudo -u deploy gpg --lsign-key <key-id>
```

Verification covers every file, not only the JSON documents: anything present in
a version directory that `hashes.json` does not list fails the deploy. A stray
`.html` or `.js` would otherwise be served from the API's own hostname.

Use `--dry-run` to see the exact change set without touching the live root.

### Automating it

```ini
# /etc/systemd/system/getbible-commentaries-deploy.service
[Unit]
Description=Deploy the GetBible commentary API
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/deploy_static_api.sh \
    --repo git@github.com:getbible/commentaries.git \
    --root /var/www/getbible/commentaries \
    --require-signature \
    --verify-url https://commentaries.getbible.net/v1/commentaries.json
```

```ini
# /etc/systemd/system/getbible-commentaries-deploy.timer
[Unit]
Description=Check for a new commentary API build

[Timer]
OnCalendar=*-*-* 05:30:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
```

The builder runs monthly, so a daily timer simply finds nothing to do most days
— the pull is a no-op, verification passes, and rsync transfers nothing.

### Why the configuration is split the way it is

`conf.d/getbible-api-http.conf` holds only the shared memory zones and the log
format, because those cannot live in a server block. Everything else is in
`snippets/getbible-api-server.conf` and included per host.

That is not tidiness. A distribution's stock `nginx.conf` already sets
`sendfile`, `gzip`, `tcp_nopush`, `ssl_protocols`, and
`ssl_prefer_server_ciphers` at http level, and nginx treats a second
declaration in the same context as a fatal `directive is duplicate` error — so
an http-context drop-in would refuse to start on a stock Ubuntu box. Server
context directives override the http-level ones instead. Keep new settings in
the server snippet unless they genuinely cannot go there.

## The caching model

Two tiers, because the documents have two very different change rates:

| Documents | `max-age` | Why |
| --- | --- | --- |
| `{resource}.json`, `build.json`, `hashes.json` | 300s | Rewritten by every build |
| Everything else | 86400s | Changes only when a source module changes |

Both carry `stale-while-revalidate` and `stale-if-error`, so a slow or briefly
unreachable origin degrades into serving slightly stale JSON rather than errors.

Nothing is marked `immutable`, deliberately. Paths are not content-hashed, so a
document's URL is stable across rebuilds; promising immutability would strand
clients on an old copy after a correction.

### Why ETags survive a deploy

nginx derives a static file's ETag from its mtime and size. A deploy that
rewrote every file — a fresh clone, or `cp -r` — would change every mtime and so
every ETag, and several thousand clients would re-download a corpus that had not
changed. Git and rsync both write only what actually differs and rsync preserves
mtimes, so unchanged documents keep their ETag and revalidation stays a 304.

This is why the script pulls into a persistent checkout instead of cloning, and
why the compressed variants are stamped with `touch -r`.

### Atomicity

`rsync --delay-updates` stages the whole change set and swaps it in at the end,
which narrows the window where a client could see a mix of old and new documents
to a fast sequence of renames. It does not eliminate it.

That is a deliberate trade. Eliminating it entirely means deploying to a new
release directory and swapping a symlink — which changes every inode on every
deploy and throws away the ETag stability above. For a read-only corpus where
old and new documents are each individually valid, and where a build lands
monthly, stable ETags are worth far more than a perfectly atomic switch.

## Behind a CDN

Put a CDN in front of both origins. The two cache tiers are already what a CDN
wants, and the bulk documents in particular should be served from the edge.

Restore the real client IP before the rate limits apply, or they will limit the
CDN rather than the client — the commented `set_real_ip_from` block in each
server file is where that goes. Refresh the address ranges from the CDN's
published list on a schedule; do not freeze them into the config.

If the origin sits only behind a CDN, consider raising the `limit_req` rate:
the limits shipped here assume the origin is directly reachable.

## Security posture

- **Read-only.** Anything other than `GET`, `HEAD`, or `OPTIONS` gets a JSON
  405. `client_max_body_size` is 1k — nothing is ever uploaded.
- **No markup anywhere.** `default-src 'none'`, `sandbox`, and `nosniff` mean a
  response that somehow was not JSON is inert in a browser.
- **No repository metadata on the origin.** The live root holds only version
  directories; the dotfile deny rule is a second line, not the first.
- **Signed input.** `--require-signature` ties the origin to the build key.
- **Verified input.** The whole tree is checked against `hashes.json` before and
  after it goes live.
- **No version disclosure.** `server_tokens off`.
- **Abuse limits.** Per-IP request and connection limits, with a tighter
  connection cap and a rate cap on the bulk documents so one offline sync cannot
  monopolise the origin's upstream.

## Verifying a live origin

```bash
scripts/verify_live_api.sh https://commentaries.getbible.net
scripts/verify_live_api.sh https://dictionaries.getbible.net
```

The script reads the catalog and follows it, so it works against any build
without being told which modules exist. It asserts every promise the API makes:
paths resolve, both cache tiers are right, precompressed variants are served,
revalidation returns 304, CORS and the security headers are present, and
failures come back as JSON. It exits non-zero on the first broken promise, so it
is safe to run from a monitor.

## Rolling back

The live root records what it is serving:

```bash
cat /var/www/getbible/commentaries/.revision
```

Generated repositories are replace-only, so the clean rollback is to revert the
bad commit in the publication repository and deploy again:

```bash
git -C /var/lib/getbible/commentaries revert --no-edit <bad-commit>
git -C /var/lib/getbible/commentaries push origin main
scripts/deploy_static_api.sh --root /var/www/getbible/commentaries
```

That keeps the history honest about what was served and when, which a symlink
flip does not.

A failed deploy needs no rollback: verification runs before the sync, so the
previous build is still live and untouched.

## Monitoring

Watch these, in rough order of how much they matter:

- `verify_live_api.sh` exit status, on a schedule.
- The deploy unit's exit status. A non-zero exit means the origin is serving a
  build older than the newest published one.
- `hashes.json` age versus the builder's monthly schedule — a stale one means
  builds have stopped, which is invisible from the API itself.
- 5xx rate and `p99` request time from the JSON access log.
- 429 rate. A rising 429 rate usually means a client is ignoring
  `Cache-Control`, not that the limits are too tight.
- Certificate expiry.
- Access log growth. The logs are named `*.access.log` so the stock
  `/etc/logrotate.d/nginx` glob (`/var/log/nginx/*.log`) rotates them; renaming
  them outside that glob silently fills the disk.

## Adding a v2 later

The version lives in the directory, not the repository name, so a future
`v2/` is published beside `v1/` in the same repository. The deploy script
already discovers and syncs every `v[0-9]*` directory it finds and retires ones
that disappear. Serving it needs a copy of `snippets/getbible-api-v1.conf` with
the paths changed, included alongside the existing one. `v1` keeps working
untouched, which is the whole reason the version segment is there.
