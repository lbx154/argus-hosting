# Hosted research trial and data review

Source: migrated from Argus `e5d12046472603d43c4ff8f6893f5611619c28a8`; maintained in argus-hosting.

The hosted service supports eleven invitation-scoped workspaces, separate model and
compute accounting, an operator dashboard, and consent-aware research replay and
dataset export. It runs on a Linux operator host; ordinary Argus desktop and Web
installations do not enable this service automatically.
On another operating system a hosted operation stops with `HostedHostRequired`,
naming the missing capability (no-follow directory descriptors, POSIX accounts,
Unix sockets with peer credentials, or `/proc` process identity), instead of
running with weaker file or peer checks. The client pieces users run on Windows
and macOS (trial setup, the desktop protocol, and the runtime opt-in hook) do
not depend on these capabilities.

## Eleventh workspace and data administrator

The original administrator workspace is enrolled as ordinary `trial-11`, alongside
the ten invitation containers. Its original state and project paths are retained.
Start this existing native workspace explicitly with
`python -m argus_trial_server.admin_runtime --portal-config /private/portal.json
--state-dir /existing/admin/state --workdir /existing/admin/workspace
--pi-bin /installed/Argus-Pi/packages/coding-agent/dist/bundle/cli.js
--tenant trial-11 --uds /private/tenants/trial-11/run/web.sock
--model-socket /private/model-socket/gateway.sock`.
Its own invitation authenticates model requests through the shared meter. Add
`trial-11` to the portal, analytics and compute configurations; analytics uses
the original `global_root` and explicit `runtime_mode: "host"`. Compute retains
the ordinary mounted-volume layout and a separate 200-GPU-hour allowance.
The default provisioning command still creates ten container workspaces; enrolling
an existing native workspace does not move or recreate its project files.

The data administrator signs in at `/admin/login` using `admin_login_token`.
This creates an `/admin` cookie independent of the ordinary invitation cookie.
Both logins can coexist in one browser, and signing out of either leaves the
other session intact. The administrator key grants data-backend access; opening
a project still requires an ordinary invitation, including for `trial-11`.

The user workbench and administration are separate builds. The hosted user
workspace is built by `frontend/workspace/build.mjs` from Argus sources plus the
hosting-owned policy patch. `frontend/admin-data` owns the independent admin
entry, dependencies and tests; it reuses existing Argus shell/UI sources during
its build. See [frontend builds](../frontend/BUILDING.md).

The data view calls `/admin/api/training/*` and does not initialize user workspace
state. Project names are joined by tenant and SID. Loading more observations
merges each episode by its recorded sequence; the loaded count is separate from
the retained total. Opening a project re-reads the ordinary invitation identity.

`argus_trial_server/data_page.py` registers page routes. Configure
`admin_frontend_dir` for `/admin/data/` and `/admin/data/assets/*`, and
`frontend_dir` for the user page and `/assets/*`. A missing administrator build
returns 503. User assets do not substitute for administrator assets.

## Mission map

The conversation, mission view, workbench and progress panel share the current
work-status projection. A live server is not itself evidence that the selected
task is executing. Paused tasks, concurrent tasks, retry waits and disconnected
browsers retain their distinct states and the time of the last associated
progress record. The workbench counts recorded work items; it does not turn that
fraction, a saved file or an ended review call into proof that the overall goal
was reached. Remaining time is shown as unknown without a comparable workload.

The conversation's **Understand this step** card reuses the map's generated-copy
cache. It explains the purpose, one relevant concept with an illustrative example,
the supported scope and the recorded next step. Explanations cite the task's
existing event identities and never change task or review state. Only meaningful
task milestones request fresh text; tool updates do not trigger repeated model
calls or background generation of the whole history. Original records remain
readable if explanation generation is unavailable. Autonomous work has its own
progress section, so a new chat question does not claim ownership of unrelated
work already in progress.

The configured hosted frontend is shared by all authenticated invitation
workspaces, including `trial-11`. Their project APIs remain separately routed; sharing
the interface does not merge projects, credentials, or conversation records.

The map shows the latest page of each mission as a single card by default.
**History** expands its earlier pages without creating new tasks or removing
events. Grouping follows durable task IDs, not similar titles. **Locate current**
opens the latest page; the part controls navigate the complete history. Running
team workers retain their actual state even on an earlier page or while the
parent mission is paused. This display change does not alter recorded training
data.

Frontend-only releases preserve embedded HTML previews on older tenant APIs,
with a visible limitation notice. The standalone interactive preview is used
when the preview response advertises `served_page: true`.

## Data and metric contracts

The dashboard separates successful HTTP message submissions, explicitly accepted
tasks, and compute submissions. A chat reply, a business error returned with HTTP
200, a duplicate dispatch, or an unidentified task is not an accepted task.
Captured task IDs connect a request to its response and matching runtime events;
nearby timestamps are never used as task identity evidence.

New hosted configurations mark all ten invitations as internal team testing, so
their activity is excluded from external traction by default. Operators must
explicitly mark genuine external trials in `analytics.json`. Re-initialization
preserves an existing analytics configuration and does not change account roles
or permissions.

Model accounting is cumulative, independently of the dashboard date filter:

- **Settled:** token usage reported by completed provider calls.
- **Reserved:** outstanding reservations for active requests.
- **Uncertain:** charges retained after unknown or interrupted usage.
- **Unattributed:** ledger balance without corresponding request receipts.

Unavailable values remain unknown. The total ledger balance includes reservations
and must not be presented as entirely measured token consumption. A hosted
`token_limit: null` means unlimited cumulative allowance; concurrency and TPM
admission still apply. The separate desktop trial keeps its own default allowance.

## Authorization and replay

The current research notice and training purposes are versioned. The portal can
record affirmative onboarding acceptance, or an operator can explicitly record an
existing team policy through the separate offline-authorization mechanism. An
offline policy is identified as operator-attested authorization, not a fabricated
browser receipt. An unknown historical effective date does not authorize backfill.

For an explicitly authorized internal team, the private portal configuration can
set `team_training_policy` with `mode: "internal_team_offline"`, selected
`tenant_ids`, and an `evidence_note` recording the owner's declaration. It activates
that policy on the live collector and supplies missing internal-training grants.
Existing grants, withdrawals and captured episodes are preserved; restarting the
service or repeating an ordinary invitation login does not reset their boundary.
This configuration does not authorize external sharing.

Members of this configured scope can enter with their invitation alone. Code-only
sign-in uses the recorded operator authorization and does not manufacture a
browser acceptance or re-enable a withdrawn training grant. For mixed
deployments, the entry form first verifies the invitation: the existing notice
form is shown only when the verified account still requires it. Accounts outside
the private policy retain the normal affirmative-onboarding requirement.

Internal training and external sharing are separate purposes. External sharing is
off by default and needs its own authorization and export review. Revocation,
notice changes and project deletion are checked again during collection/export.

The persistent collector works without a browser tab. It records selected public
HTTP and runtime observations, with stable identities, ingestion sequence,
truncation and gap markers. Initial observation and source replacement establish
a collection boundary; existing bytes are not retrospectively imported.
Sequence is ingestion order, not a proof of causal order or successful work.

The running portal holds an idle WAL connection and uses SQLite's `NORMAL`
synchronous mode for analytics. This prevents every streamed observation from
forcing a disk flush and every short connection from checkpointing on close.
Registration indexes the current project instead of replaying the whole tenant;
database checks in request middleware run outside the asynchronous event loop.

Project research copies can be deleted with a tombstone that disables future
collection for that project. Research retention is at most 30 days, with additional
capacity limits. Source workspace/runtime files, accounting, separately retained
consent/checkpoint metadata, downloaded exports and independent backups have
separate lifecycles. Logical deletion is not secure disk erasure.

## Dataset review

The current data workbench at `/admin/data` collects Manager, Planner, Engineer
and Reviewer observations before quality review. It retains actual application
system/developer inputs, visible model output, provider requests, tool definitions,
arguments and results. Zero-tool, failed and interrupted calls retain their
observed records. Structured hidden reasoning and signatures are omitted.
Collection status and quality acceptance are separate; incomplete or sensitive
observations are not grounds to discard an entire current-policy episode.
The workbench's observation export does not require per-sample approval.

The following strict candidate-review and SFT export workflow remains available
for legacy samples and separately reviewed datasets; it does not gate the
current observation collector.

The operator page at `/admin` supports selected-project previews, purpose filters,
pagination, explicit per-event quality review, and export auditing. The tester data
page explains the current permissions, recorded authorization and revocation
controls. Previewing candidates does not automatically approve them for training.

The pinned Pi observer records actual public context, tool definitions, provider
request projections, model arguments, executed arguments and tool results.
Provider strict-schema conversion and optional-null argument removal are checked
against the pinned runtime behavior. Generated comparison values never replace
the captured provider schema or model arguments in training samples. Unsupported
coercions, incomplete/truncated tool episodes and sensitive content are quarantined.

The observer excludes system instructions, hidden reasoning, private signatures
and credentials. A complete public tool episode is not the complete model context.
Tool SFT export additionally requires explicit review that the public episode is
self-contained. Exports distinguish `human_operator`, `automated_acceptance` and
`unspecified` review; automated acceptance names its evidence file and is not
reported as human review. No dataset is uploaded or training job started by export.

Per-event review receipts bind the selected purpose, event, sample content and
reviewer attribution. Later exports keep each sample's original review and
evidence, even when a different reviewer creates the export.

## Offline package validation

After downloading a dataset ZIP, validate it locally with the standalone module:

```sh
python -m argus_trial_server.training_validate /path/to/dataset.zip \
  --require-agentic \
  --evidence /path/to/acceptance-report.json \
  --report /path/to/validation-report.json
```

`--evidence` supplies the exact original file whose size was recorded during
automated acceptance. Repeat it for samples reviewed against different reports.
Every automated export or sample review requires its matching evidence file;
declaring an evidence size alone is insufficient. Human reviews without an
evidence file do not require this option.

The command reads the current ten-file export ZIP directly and checks its file
set, sizes, JSON/JSONL structure, tool schemas and arguments, captured
call/result correspondence, sample splits, counts and review attribution. It
does not extract files, call tools, contact a model or launch training. Packages
and their total uncompressed contents are limited to 64 MiB; up to 256 evidence
files are accepted, each at most 32 MiB.

The JSON report always appears on standard output. `--report` also writes the
same report atomically to a separate file and cannot overwrite an input package
or evidence file. Reports contain sizes, counts, fixed error codes and locations;
they do not repeat sample text or tool arguments.

Exit status `0` means `valid: true` and, when `--require-agentic` is present,
`agentic_training_ready: true`. Status `1` means validation failed or the required
reviewed tool samples are absent; status `2` identifies invalid CLI arguments.
A valid chat-only or empty package is not ready for agentic tool training.
Keep `--require-agentic` when this distinction must fail a pipeline. An empty
validation split can be intentional for a single tester group.

The report establishes package consistency and correspondence to the included
observations. It does not prove that the runtime was untampered, that declared
human review occurred, or that scientific conclusions, proofs, benchmarks and
licensing claims are correct. Public episodes omit system instructions and
private reasoning. Keep the original evidence and deployment/task receipts for
separate verification, and evaluate exported samples for the intended model and
chat template before training.

## Operator setup

Follow the [complete installation, container build, service startup and upgrade
commands](deployment.md). Install Argus and this repository into the same
interpreter, then build both frontends. The hosting root package installs
`argus_trial_server` and `argus_hosting.trial`; there is no subpackage install.

Keep private state and credentials outside both source checkouts and all build
contexts. Initialize a new deployment with both `--frontend-dir` and
`--admin-frontend-dir`. Existing deployments preserve their ledgers, master key,
workspace paths and authorizations; change their asset settings deliberately.
Published releases record the hosting commit, Argus commit, image tag and both
asset directories. Roll the backend before switching assets, after active work
finishes; `start-containers` alone never upgrades an existing container.

The hosted observer profile requires the checked Pi 0.85.1 runtime. Container
builds use the hosting checkout as context and an explicit `argus-source` named
context for Argus. Native preview startup uses `--source` for Argus and
`--hosting-source` for this repository; see [native preview](../deploy/README-native-preview.md).
For the existing `trial-11`, keep its original state and project directories,
install both packages, and use the explicit administrator-runtime command above.
`--argus-source` is an optional source-precheck override; installed Argus is the
default. Verify `/api/meta` after cutover, including that native workspace.

The service installer `deploy/web_services.py` creates only this deployment's
five user units: portal, meter, compute scheduler, egress proxy and relay guardian.
It enables them but leaves starting them to the operator. Storage mounts,
reverse-proxy configuration and resource allocations depend on the target host.
The compute scheduler has its own image and accounting; queued time does not
count as execution. No old host work should be terminated to create capacity.

## Capture capacity and public references

Legacy strict Pi episodes retain at most 16 MiB of cumulative observations and 512
observations. Repeated public contexts count toward that total. Each projected
event and observer RPC remains limited to 4 MiB, with an 8 KiB transport envelope;
individual public text stays at 256 KiB and tool results at 64 KiB. Retained hosted
episodes share the existing 128 MiB storage budget. Exceeding a limit quarantines
the episode; it never silently truncates a training sample.

Preview/source selection and generated export packages are each capped at
32 MiB. Export checks both expanded contents and ZIP bytes. Export large projects
separately when their combined package exceeds the limit. The offline validator
retains its independent 64 MiB ZIP and expanded-content ceiling; that does not
increase the exporter limit.

In that legacy strict workflow, standard platform directory references are recognized as public path literals,
with project and mission references bound to the observed runtime. Published
Skill files are resolved from the built-in package inventory, and actual `read`
results must match the shipped body or its exact requested line selection.
Unknown or modified global files and raw model logs are not admitted by these
exceptions. LaTeX escapes are distinguished from actual UNC server/share paths;
Windows drive and device paths remain sensitive. Credential/environment dumps
continue to be filtered or quarantined. Quarantines retain only fixed event kind,
structural field and detector labels for diagnosis, never matched content.

## Validation

For repeatable ordinary-user production tasks and the independent acceptance
workflow, see [Agentic training examples](examples/agentic-training/README.md).

Hosting CI checks out and installs Argus `dev` first, then installs `.[dev]`
and runs all tests in this repository. Local checks, after installing both
packages, are:

```sh
python -m ruff check argus_hosting argus_trial_server deploy tests scripts/pr-gate experiments
python -m pytest -n 4 tests --basetemp=build/pytest-temp
python -c "import shutil; shutil.rmtree('build/pytest-temp')"
```

Management and hosted workspace typechecks, tests and actual builds are described
in [frontend/BUILDING.md](../frontend/BUILDING.md). Desktop assembly and checks
are described in [desktop/README.md](../desktop/README.md).

The actual Pi CLI test is opt-in with `ARGUS_TEST_PI_DIR` pointing to the pinned
local Pi checkout. It uses a synthetic local provider and temporary workspace,
not production credentials or user tasks. Production acceptance must separately
verify the deployed source/image, ordinary invitation routes, actual tool/artifact
results, correct project association and truthful export/review metadata.
