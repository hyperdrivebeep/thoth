# Local retrieval preparation

The local settings page prepares fixed model files and a project's derived embedding index.
Local retrieval is ON for every project unless a person switched it off: a project with no stored
setting reads as enabled (generation 1), and a stored setting, on or off, is never replaced by that
default. Preparation does not activate a shadow or canary or run a research request. A project
that has it on, with its index ready, uses the index in its research: see "Applying the index to a
project's research" below. Indexing needs the optional runtime and the model files; without them
nothing is indexed and research uses the lexical selection.

## Install and import

Install the optional native runtime with `uv sync --frozen --extra local-retrieval`. This extra
contains ONNX Runtime, tokenizers and NumPy. Ordinary startup does not import these libraries,
load weights or start an encoder. File import also works without this extra. Preparation
reports `EMBEDDING_DEPENDENCY_UNAVAILABLE` until the dependencies are present.

The shipped manifest fixes Granite's revision, ten paths, sizes and SHA-256 values
(123,586,186 bytes total), including notices and tokenizer/pipeline configuration.
Import an existing local copy through Settings → Local retrieval preparation, or:

```powershell
thoth local-model import C:\local\granite-copy --workspace C:\local\thoth-workspace
thoth local-model status --workspace C:\local\thoth-workspace
```

These commands only prepare/check files. They do not enable indexing or start inference.
Assets are stored under the chosen workspace's `models` directory, in an immutable
model/revision/manifest-digest directory. Existing invalid installations are preserved and
reported as failed. Links, hard links, reparse paths and network shares are rejected.
Handles, sizes and hashes are checked. A separate publisher lock protects an atomic move
from an owned staging directory; READY installations are never silently overwritten.

Downloading needs an explicit checkbox and the displayed manifest digest. Only the fixed
revision/files are fetched over HTTPS (port 443, no user information, at most four redirects).
The host must be exactly `huggingface.co` or `hf.co`, or a subdomain of either (compared as
whole, lower-cased names, so `evilhf.co`, `evilhuggingface.co` and `hf.co.evil.com` are refused).
Hugging Face moves large files between CDN names (for example `cdn-lfs.huggingface.co` or
`us.aws.cdn.hf.co`), so the CDN name is not listed; within
one file the first CDN name is pinned and a second one is refused. The host is not what keeps the
files honest: every file's size and SHA-256 are checked against the pinned manifest. Content sizes
and hashes are bounded. No caller URL, package name, factory name or credentials are accepted;
environment proxies and cookies are not used. This narrow install grant does not enable research
network access. Tests use synthetic HTTP streams. One real installation through this route from
Hugging Face was run on 2026-11-04 (123,586,186 bytes in about 7.6 seconds on the author's PC);
that is a single observation, not a promise about every network.

## Preparation and state

`model/localAsset/read` reports file readiness, dependencies, worker loading and active
transfer separately. `project/retrieval/experiment/read` reports the current project's
generation and authorized source/job counts. Reads never enable or schedule work.
Both are query-route methods; all seven preparation methods are LOCAL-only.

Asset methods use `project_id: "system:workspace"`. `model/localAsset/import` requires
`source_path` and `manifest_digest`; `model/localAsset/install` requires `manifest_digest`
and literal `confirm_download: true`. Project methods require their `project_id`;
`configure` additionally requires `enabled`, and retry names its job with `job_id`.

`project/retrieval/experiment/configure` requires `expected_generation`. Disabling cancels
the owned preparation. `prepare` also requires that configuration generation; the explicit
`project/embeddingIndex/retry` requires the failed job's own generation and ID. Project
authority and current resource scopes are checked before work and where S2 publication
consumes source data. Authenticated workspace file writes require write and admin authority.
Hosted requests fail before local asset IO.

Long actions use the existing async RPC, operation poll and cancel routes. Checking an
uncertain response retains its key/operation ID; a deliberate new attempt uses a new key.
A client timeout means unconfirmed, not cancelled. Switching screens aborts only the client
wait and cannot attribute old results to a new project. The existing authenticated HTTP
deployment requires operation RPCs rather than the unauthenticated local poll route; this
card targets the normal local loopback workspace.

READY files, a loaded encoder and READY source indexes are distinct. One explicitly prepared
child uses CPU batch 1, fixed tokenization, CLS pooling and float32 L2 normalization. Its load
budget is at most ten seconds. S2 encode/job budgets remain cumulative; loading occurs before
claiming a source job. Windows Job memory remains 512 MiB. BLAS initialization uses one
thread; ORT stays intra-op 4/inter-op 1. The encoder receives verified bytes rather than
reopening a mutable model path.

Passage length is bounded. No more than 512 tokens (one window: 510 content tokens plus the two
special tokens) reach the model in one call; queries are never cut and a passage that fits is
encoded exactly as before. Attention memory grows much faster than linearly with length: a 48,985
character page container took 33 GB in an unbounded run, and before the bound such a page failed its
source job with EMBEDDING_FAILED at the 512 MiB commit limit (worker peak commit 504 MiB). Measured
in the real worker: a 512 token passage peaks at 406 MiB commit and a 1,024 token passage at
477 MiB, so 512 keeps a margin of about 100 MiB under the limit. A local check on 2026-11-06 with the
same model file (another process, so only increases compare) saw +136 MiB at 512, +299 MiB at 1,024
and +1,125 MiB at 2,048 tokens. A longer passage is therefore encoded as overlapping windows, one
window per worker call. Window 0 is the first 510 content tokens, exactly the input version 1.1.0
used; each later window starts 382 tokens after the previous one (128 tokens overlap), up to eight
windows (3,184 content tokens). A passage scores as its best window (MaxP, `max_window_score`), so
the evidence span, which is the citation unit, is unchanged and `DenseMaterial` still carries one
score per span. Text past the eighth window is not encoded and the vector records that it was cut.
Window 0 is stored under the usual content key and window n >= 1 under a derived key
(`window_key`) in the same `embedding_vectors` table; the schema did not change. The first window
records the passage's size in `source_tokens`, and a reader computes the number of windows from it
(`window_count`), so a missing window makes the passage incomplete. The ready manifest lists every
window's hash (a one-window passage is listed as before) and orphan collection keeps every window
key of a passage a current job names. The index stores per vector `embedding_vectors.truncated` and
`source_tokens`: truncated is 1 for a passage with content past its last window, 0 for one whose
every token reached the model, NULL where nothing was recorded (vectors made before the record
existed, or by a worker that does not say); source_tokens is the passage size when the passage
needed more than one window or was cut. A vector that is already stored keeps its record. The
worker also still counts cut passages in memory (truncated_passages). The preprocessing version
names the geometry (`granite-cls-l2-batch1-question-only-win510o128m8-cap512:1.2.0`; a test keeps
the numbers equal to the constants) and every content key includes it. Raising the version to 1.2.0
did not touch the installed model files: the packaged manifest keeps its own identifier
(`GRANITE_ASSET_PREPROCESSING`, still `…:1.0.0`), so the installed folder, named by the manifest
digest, is found as before and nothing is downloaded again. Rows saved under 1.0.0 or 1.1.0
(settings, jobs, vectors) still load: a saved setting is read as the current version, so the old
jobs stop matching, the ordinary reconcile marks them STALE, and an explicit "Prepare index" builds
the sources again under 1.2.0. Old vectors stay in the table unused until orphans are collected.
Until then the project card says the index must be rebuilt (`outdated_index`).

Interrupted operations are not automatically rerun. Files can be checked independently;
explicit prepare recovers expired S2 leases and reconciles sources. An old operation receipt
can remain unresolved after a server crash. Current file/index state does not prove that
receipt succeeded. Recovery never automatically calls a research model.

## Applying the index to a project's research

A project that has local retrieval on (the default) gets its own retrieval policy for a research
request, and only while every active source of the project has a current READY index. The check
(`application/services/dense_opt_in.py`, `ProjectDenseOptIn`) is made for each request: local
retrieval enabled in the project's configuration, the optional runtime installed, and every ACTIVE
source binding has a READY job of the current configuration whose source is still current (`is_current_header`:
configuration, active binding, and the latest version's identity and hash; reading every span for
this check took 1.3-1.5 s per question for a 19,803-span project, and the request checks each span's
text hash again when it scores). A project
that is off, has no index, has an index in progress or failed, has a source that changed after
indexing or was added after it, or has any source not yet READY, is not applied: it keeps its
baseline and its journal entries and digests are the ones it had before this existed.

When the check passes, `BehaviorSnapshotService.prepare_work` replaces the retrieval snapshot of
that one execution, after exposures were selected, with a `RetrievalRoutingPolicy` 5.0.0 whose
`exposure` is `PROJECT_OPT_IN` and whose origin is `PROJECT_OPT_IN` (reason
`LOCAL_RETRIEVAL_ENABLED`). `max_spans` and `character_budget` are those of the baseline it replaces.
The replacement is made only when the selected snapshot is the plain 2.0.0 baseline: an armed
canary, or a baseline the project promoted, is left as it is. `capture` and `baseline_snapshot`
never change, so evaluation still compares against the baseline. Only the snapshot service makes a
`PROJECT_OPT_IN` policy; the routing handler refuses it in stored content, so no artifact or
exposure can carry it. Switching off, a stale index or a new unindexed source takes effect on the
next question; a request already running keeps the snapshot it started with, and the execution
record shows it.

For a project in this state, a question whose dense scores cannot be used (the encoder is not
running yet, it timed out, the question is too long, the resident copy is over its limit, a source
of the frame is not in the index, the optional runtime is missing) is answered with the lexical
selection the baseline would have made, byte for byte, and the routing observation records the
reason (route `LEXICAL`, `dense_used` false, the `fallback_reason`). The result differs from the
baseline only for a question whose dense scores were actually used. The older analysis entry has no
dense scores and treats this policy as the lexical baseline with the same budgets.

Applying the index changes the retrieval snapshot digest, so a stage saved before a project was
switched on, or before its index became ready, is not reused after it. The rule did not meet its
promotion criteria on two evaluation sets (see `docs/BETA.md`); it is applied to every project with
local retrieval on by decision, and the per-project switch is the way to turn it off.

## Automatic indexing

Nothing has to be clicked. `AutoIndexer` (`application/services/auto_index.py`) looks at every
project that is not closing or archived when the HTTP server first builds the runtime (the first
request after the server starts; the TUI and the CLI do not start it), when the model files were
just installed or imported, when local retrieval is switched on again, and every 60 s (a source that
was just connected is found at the next tick, so within about a minute). A project needs work when
it is on and has a pending job or an active source with no job of the current configuration
(`needs_indexing`, which reads no span). A job that failed with a real error or that a person
cancelled counts as covered and waits for a retry; a project whose run failed is left alone for
300 s. It does nothing while the optional runtime is missing, and it reads the model descriptor only
when it has work and no encoder is bound yet.

Work that the system itself interrupted is taken up again without a click, and is told apart from
the cases above:

- A pass has a time limit (the budget's `job_timeout_ms`, counted after the jobs are queued). A job
  the limit cuts off goes back to `PENDING` with its stored vectors (`store.interrupt`) instead of
  `FAILED`, and the next pass goes on. A job's own time limit still fails it.
- Closing the service cancels the running jobs with the reason `SERVICE_CLOSED`, apart from
  `USER_CANCELLED`, the mark of a person's cancellation.
- A job a crash left `RUNNING` once its lease ended, one already recorded as
  `LEASE_EXPIRED_NO_AUTORETRY` and one cancelled with `SERVICE_CLOSED` are queued again by
  `resume_interrupted`, which every pass runs before it asks which projects need work.

It runs one project at a time in the one indexing slot of `LocalRetrievalService`, shared with the
explicit "Prepare index" command. It never starts while the slot is taken. An explicit command that
arrives while it works waits for the job in hand (the indexer stops between jobs, nothing is
cancelled) and then takes the slot; two explicit commands still conflict
(`EMBEDDING_INDEX_BUSY`). Switching a project off cancels its run, and closing the service stops the
loop. After a project's jobs are all run it drops the vectors no job refers to any more
(`collect_orphans`).

`reconcile` and the read of a job's source read every span of the project one by one; for a
19,803-span project that held the event loop for 45 s. `reconcile` now reads the project's evidence
once and groups it by source, `areconcile` and the read of a job's spans run in a worker thread, and
the change notice that touches loop-owned state is given back on the loop.

The event loop is free meanwhile, so what runs in those threads must not hold the database write
lock while it reads: a write the loop makes would wait for the lock and fail with "database is
locked" after 5 s. Publishing a job reads the source and the stored vectors first
(`prepare_publication`, no lock) and the short write transaction only checks that the binding, the
latest version and its hash are still the ones that were read before it marks the job `READY`; a
source that changed meanwhile is not published. `store.reconcile` and `collect_orphans` compare the
jobs with their sources before taking the lock (`collect_orphans` deletes only if no job moved in
between). What still happens under the lock on the loop is the per-span checkpoint commit of a
running job and `claim`.

## Dense scores in the research path

`DenseMaterialProvider` (`application/services/dense_material.py`) gives the research path a
`DenseMaterial` for the original question. It is asked only when the project has local retrieval
on and the active or a shadow retrieval policy is a `RetrievalRoutingPolicy`; it is not
asked for a frame with one source (routing sends it to the lexical lane). Nothing else changes:
a project switched off gets no material and its selections, journal entries and digests
are the ones it had before.

Scoring reads a resident matrix: all windows of the READY sources of a project as float64, one
copy per project, at most two projects and 128 MiB (43,690 windows). It is read once when the
index is READY and dropped when the index state changes. The state is a stamp read from one SQL
query per question (`index_stamp`: enabled, config generation and the id and generation of every
READY job); a new READY job, a job that leaves READY, a reconfiguration and a switch off change
it. `EmbeddingIndexService` also reports configure, reconcile and each job's end through
`on_change`, which drops the copy at once. Turning a project off through
`LocalRetrievalService.configure` is noticed on the next question. A passage scores as its best
window, equal to `max_window_score` (the reference): a score within 1e-12 of a rounding boundary
or of zero is computed again the reference way, so the nine-place strings match. numpy is used only
if the `local-retrieval` extra is installed.

The question is encoded by the already running worker with the 2 s query timeout. A worker that is
not running (for example right after a restart, because preparation only starts it for sources
still waiting to be indexed) is started in the background once per 60 s and the question goes on
without dense scores meanwhile.

Whatever prevents dense gives a material with `unavailable_reason`, which routing records as the
`fallback_reason` and answers with RRF(LEX, GB). A project that is off gets no material, so its
reason stays `DENSE_MATERIAL_MISSING`.

| Situation | Reason |
| --- | --- |
| numpy missing | `EMBEDDING_DEPENDENCY_UNAVAILABLE` |
| no READY source | `INDEX_PENDING` (also: resident copy still being read after 5 s) |
| encoder not running | `EMBEDDING_NOT_PREPARED` |
| a frame span is not in the index | `INDEX_INCOMPLETE` (also: a READY source cannot be read) |
| a frame span's text changed after indexing | `INDEX_STALE` |
| resident copy over 128 MiB, or more than 20,000 spans | `INDEX_TOO_LARGE` |
| question longer than the worker accepts | `QUERY_TOO_LONG` |
| encoder exceeded the query timeout | `EMBEDDING_TIMEOUT` |
| any other encoder or index failure | `EMBEDDING_FAILED` |

Selection speed: the source digest of a frame and the digest of a whole material are computed
without the generic canonical walk but give the same bytes (tests pin this), the digest of the
frame just bound is remembered for the very same span objects so binding and checking compute it
once, and a prepared material builds its scores without per-score validation because the producer
has already clamped and rounded them.

## Verification limits


The bounded local smoke uses existing pinned files and synthetic Korean/English text:
twelve encodes across three workers on Python 3.12/3.13. It checks loading and deterministic
vectors, not retrieval quality, full-product latency or P2 readiness. The Python socket guard
is defensive, not an OS egress sandbox. File installation, scopes, cancellation and replay
are separately covered by controlled tests. Exact results belong to the change's receipt.
