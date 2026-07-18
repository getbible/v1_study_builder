# Target repository setup

Create two empty repositories with `master` as their default branch:

- `getbible/v1_commentaries`
- `getbible/v1_dictionaries`

Seed each repository with a README and commit it before the first builder run.
The builder owns only the `v1/` directory; repository documentation and server
configuration outside that directory are preserved.

Add these Actions secrets to `v1_study_builder`:

| Secret | Purpose |
| --- | --- |
| `GETBIBLE_GIT_USER` | Commit author name |
| `GETBIBLE_GIT_EMAIL` | Commit author email |
| `GETBIBLE_GPG_KEY` | ASCII-armored private signing key |
| `GETBIBLE_GPG_USER` | Signing-key identity |
| `GETBIBLE_SSH_KEY` | Deploy key/private key with write access to both targets |
| `GETBIBLE_SSH_PUB` | Matching public key |

The public `getbible/getbiblesword` release requires no Actions secret. The
default target URLs are already compiled into the CLI. Set the optional
`GETBIBLE_COMMENTARIES_REPO` and `GETBIBLE_DICTIONARIES_REPO` secrets only when
using forks, staging repositories, or non-GitHub remotes.

Protect `master` on both generated repositories against manual changes, while
allowing the builder deploy key to push. Serve the repository checkout directly
with Nginx; no application runtime is required.

The production workflow always completes the requested build. It invokes the
publication steps only when all six Git author/signing/SSH values are present and
the workflow's `push` input is enabled. Missing publication secrets therefore do
not turn an otherwise valid build into an accidental push or a workflow failure.
