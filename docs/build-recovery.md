# Build reports and recovery

A full build attempts every policy-approved module in the selected resources.
Each module is extracted, checked, and written in isolation. A failed module never
contributes partially written documents to another module or to a replacement tree.
Completed modules remain available when another module or publication fails.

## Outcomes

The JSON report has schema `getbible-build-report-v1`.

| Status | Meaning |
| --- | --- |
| `running` | A checkpoint; the run has not recorded a completed outcome. |
| `success` | All approved modules completed and all requested later steps succeeded. |
| `partial` | At least one resource has safe new output, but some modules or resources could not be updated. |
| `failed` | A shared prerequisite or finalization/publication operation failed, or no resource has safe new output. |

A `partial` build returns exit code zero and emits a warning. This includes a build
where one resource is blocked from replacement but another can safely be updated.
A fatal outcome returns nonzero; successful compilation remains in the recovery
artifacts. A successful local run without `--push` does not imply remote publication.

The report keeps these distinctions explicit:

| Field | Meaning |
| --- | --- |
| `built` | Newly completed modules, grouped by resource. |
| `failed` | Rejected modules, with resource, module, stage, error type, and reason. |
| `retained` | Modules whose previous verified output is carried forward, with a reason. |
| `errors` | Shared failures, blocked resources, or publication errors, with stage and reason. |
| `skipped` | Modules excluded by redistribution policy and the policy's reason. |
| `diagnostics` | Extractor diagnostics associated with modules. |
| `commits` | Local publication commit results; a commit alone is not proof of a successful push. |

`.work/reports/latest.json` is checkpointed while work proceeds and updated after
publication. Each finalized API also includes `v1/build-report.json`, validated
against its published schema and covered by `hashes.json`. That public snapshot
records compilation before Git publication; its `commits` is empty. Consult the
workflow's local report and GitHub job outcome for later commit or push failures.

## How previous output is preserved

For publication, the prepared output repository is the authoritative prior tree.
For a local build, an existing `dist/{resource}/v1/` can supply previous output.
A failed module's prior documents must pass integrity and schema checks before
being retained. Its existing metadata and catalog record remain intact; it is
listed under `retained`, never counted as freshly `built`.

If the failed module has no previous published output, its failure is recorded and
the safely built modules may still form the new tree. If prior output exists but
cannot be verified safely, the affected resource is blocked from replacement;
independent resources can still complete. A resource with no newly completed
modules is left unchanged, rather than being replaced with an empty catalog.

A restricted `--module` selection remains a diagnostic build and cannot be pushed.
The resilient publication policy applies to full selections; it never authorizes
replacing an API with the output of a hand-picked module subset.

## Workflow artifacts

The production workflow uploads these artifacts after ordinary build or publication
failures as well as successful runs:

| Artifact | Contents | Retention |
| --- | --- | --- |
| `study-builder-report-{run_id}-{run_attempt}` | `latest.json` when available, readable `summary.md`, and the build command's `build.log` | 30 days |
| `study-builder-commentaries-{run_id}-{run_attempt}` | Generated commentary `v1/` contents available at the end of the run | 14 days |
| `study-builder-dictionaries-{run_id}-{run_attempt}` | Generated dictionary `v1/` contents available at the end of the run | 14 days |

The GitHub job summary lists failures and retained modules and emits a warning for
partial completion. Download the full JSON for details omitted from its shorter
table. An artifact is omitted when no corresponding output exists.

Only reports and generated JSON are uploaded. Repository checkouts, raw module
packages, tool caches, signing material, and the process environment are excluded.
Configured repository URLs are redacted from the downloadable command log.

An abrupt runner loss or forced timeout can prevent the final upload steps. A report
that still says `running` is a checkpoint, not a completed build. The presence of
some generated files alone does not prove that a resource was finalized.

## Investigating and recovering a run

1. Download the report artifact and inspect `summary.md` and `latest.json`.
   Start with `failed` for module problems and `errors` for shared or publication
   problems. The stage distinguishes extraction, reference handling, writing,
   retention, and publication failures.
2. Confirm which modules are newly built and which are retained. Read
   `build.log` for the associated exception and surrounding context. Preserve
   the artifacts before their retention period expires.
3. To reproduce one module after correcting its problem, run a diagnostic build:

   ```bash
   study-builder build --resource commentaries --module Sentiment --refresh
   python scripts/validate_build.py --resource commentaries --module Sentiment
   ```

4. To inspect preserved commentary output, extract the commentary artifact into
   `recovered/commentaries/v1/`, then run the validator with a module that the
   recovered catalog lists:

   ```bash
   python scripts/validate_build.py --dist-dir recovered --resource commentaries --module Clarke
   ```

   Use `recovered/dictionaries/v1/` and the corresponding resource/module for a
   dictionary artifact. The validator checks the tree's complete hash manifest,
   OpenAPI linkage, and the chosen module's generated shape. Missing final
   catalog, schema, OpenAPI, report, or hash files mean the artifact is recovery
   material and is not yet a complete API tree.
5. If compilation finalized and only signing or pushing failed, the verified
   generated tree can be reviewed and published without re-extracting the corpus.
   Check the latest target branch first and reconcile any changes made since
   the failed run. A resource marked blocked must not be promoted merely because
   its artifact contains some valid modules.
6. After a code or source-data correction, run the full selected resource build
   for normal publication. A rerun starts a new build; it does not silently
   promote a previous artifact or erase the evidence in that run's artifacts.

Reference coordinates are never guessed to make a build pass. Nonconsecutive
chapter and verse numbers are supported directly from the Bible API; genuinely
unknown or unverifiable inputs remain visible failures. Contract, digest, schema,
path-safety, redistribution, and shared configuration checks are preserved.
