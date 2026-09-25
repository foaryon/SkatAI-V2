# SKATAI V2 — PROGRESS-DRIVEN CONTINUE / RESUME PROMPT

## 0. Purpose

Continue autonomous execution of **SkatAI V2** from the latest verified real state.

This is a recovery, course-correction, and execution-control prompt. It is **not** a historical checkpoint, a replacement specification, or an instruction to restart work merely because it has been received.

It must remain valid throughout the project lifecycle:

```text
bootstrap
→ clean V2 isolation
→ data qualification
→ immutable B0 reproduction
→ B1 bidding decision
→ RL / belief / search / league work
→ ISS validation
→ autonomous operation
→ product integration
→ release packaging
→ FULL-AFK validation
→ steady-state autonomous improvement
```

Read and obey these governing documents before substantive work:

```text
SKATAI_V2_FOUNDING_SPECIFICATION.md
SKATAI_V2_WORK_PROMPT.md
```

This prompt defines how to recover current truth, protect valid work, detect and correct drift, choose the next highest-value action, and continue until the defined finish state is demonstrably achieved.

---

## 1. Immutable Mission

Build and operate a clean, reproducible, deployment-valid, continuously improving Skat AI system that:

```text
uses one stable SkatAI product interface
supports bidding, declaration, discard, and cardplay
uses trusted, leakage-safe data
trains and evaluates bounded challengers scientifically
promotes only reproducibly accepted deployment-valid improvements
uses self-play, RL, belief, search, and other methods only when evidence justifies them
competes and validates externally on ISS where appropriate
mines external failures without contaminating frozen tests
recovers automatically from routine failures
controls compute, storage, and cost
packages accepted releases reproducibly
integrates accepted releases into the user's Skat software
operates FULL-AFK and continues producing useful challengers
```

Primary optimization target:

> **Reproducibly accepted, deployment-valid real Skat playing strength per useful compute and wall-clock time.**

The product is the Skat AI and its usable release path.

Infrastructure, dashboards, automation, observability, orchestration, and documentation are necessary only to the degree that they enable, protect, measure, deploy, or sustain that product mission.

---

## 2. Authority, Scope, and Precedence

### 2.1 Governing authority

The founding specification is binding.

The work prompt defines the required end-to-end program and finish definition.

This continue prompt governs recovery, prioritization, course correction, and autonomous continuation.

### 2.2 Domain-specific authority

Do not apply a simplistic universal rule such as “the newest file wins.” Determine authority by the question being answered.

```text
Question                                      Primary authority
--------------------------------------------  ---------------------------------------------
What V2 is allowed to be                      Founding specification
What final capabilities are required          Work prompt
What source/configuration exists              Verified current V2 Git state
What artifact/data/model exists and its hash  Authoritative persistent artifact storage
What process/job is currently running         Verified live physical/runtime state
What scientific decision is valid             Verified V2 provenance + applicable gates
What must happen after interruption           Reconciled live effects + durable evidence
What V1 material may be used                  Founding salvage gate + explicit allowlist
```

A live process proves that it exists or ran. It does **not** alone prove scientific validity, reproducibility, acceptance, promotion eligibility, or deployment fitness.

A Git commit proves source history. It does **not** alone prove that an artifact was produced, a job completed, or a scientific claim is valid.

A file named `CURRENT`, `FINAL`, `ACCEPTED`, `PRODUCTION`, or `V2` has no special authority without identity, provenance, and relevant verification.

### 2.3 Conflict resolution

When sources disagree:

```text
1. Preserve all evidence; do not erase the discrepancy.
2. Identify the exact claim in conflict.
3. Reconcile commit IDs, hashes, timestamps, lineage, job IDs, and physical effects.
4. Treat unsupported scientific claims as unproven.
5. Treat interrupted material effects as OUTCOME_UNKNOWN until reconciled.
6. Quarantine disputed artifacts or claims from promotion/training use where required.
7. Continue independent safe work.
8. Persist the discrepancy and its resolution.
```

Core invariant:

> **V2 inherits evidence, not architecture.**

Everything predating the V2 project is Legacy unless individually qualified under the founding specification.

---

## 3. Receipt Semantics: Continue, Do Not Reset

Receiving this prompt is **not** an instruction to stop, restart, replace, or invalidate all current work.

Before changing active work, reconcile it against:

```text
binding V2 invariants
verified current state
the nearest product/scientific gate
the final mission
resource and safety constraints
```

For every active job, experiment, implementation, pending change, worker, campaign, or external action, classify it:

```text
CONTINUE
  Valid, bounded, authorized, and advancing a required gate.
  Preserve state and allow it to proceed.

CORRECT
  Objective remains useful, but the implementation is flawed, excessive,
  non-reproducible, unsafe, or counterproductive.
  Preserve valid evidence/output and apply the smallest safe correction.

CONCLUDE
  Enough evidence exists for ACCEPT, REJECT, or INCONCLUSIVE.
  Finalize evidence and decision; do not extend work without decision value.

SUSPEND
  Violates an invariant; threatens data/evaluation integrity, access, or cost;
  creates duplicate material effects; lacks a credible useful path; or must wait
  for a real dependency.
  Stop further effects safely, reconcile current effects, and preserve evidence.

UNKNOWN
  State or outcome cannot yet be established.
  Inspect before repeating, promoting, deleting, changing, or launching related work.
```

Do not terminate a valid training run, reset a valid experiment, rewrite working infrastructure, or abandon an almost-complete evaluation merely because this prompt was received.

Do not continue sunk-cost work merely because time or compute was spent.

Continue from the furthest **verified valid** state, not the furthest remembered, narrated, or merely running state.

---

## 4. Progress Kernel

### 4.1 Decision question

For every substantive choice, ask:

> **Which safe, authorized, bounded action most increases the probability of the next defensible, deployment-valid improvement in Skat playing strength, or removes a demonstrated dependency preventing such an improvement?**

### 4.2 Nearest consequential gate

Identify the nearest incomplete gate on at least one valid path to:

```text
trusted data
→ reproducible baseline/champion
→ bounded challenger or product capability
→ legality / leakage / integrity validation
→ deployment-matched gameplay evaluation
→ ACCEPT / REJECT / INCONCLUSIVE
→ atomic promotion or retained incumbent
→ reproducible release / stable integration
→ autonomous repeatability
```

A gate is consequential if completing it either:

```text
enables a valid scientific decision;
removes a verified blocker to a valid scientific decision;
protects a required invariant or unique evidence;
enables a required production capability;
or demonstrates required autonomous/recovery behavior.
```

### 4.3 Action priority

Choose among executable actions using this order:

```text
P0  Prevent irreversible harm: evidence loss, leakage, corruption, credential exposure,
    invalid promotion, destructive cleanup, duplicate external effect, or loss of access.

P1  Reconcile unknown outcomes and preserve/recover valid active work.

P2  Unblock the nearest consequential science/product gate.

P3  Run a bounded action that yields a decision, rejects a false path, or materially reduces
    uncertainty on the critical path.

P4  Build or repair infrastructure only when it is a measured blocker, required invariant,
    required finish capability, or necessary to make active work reliable/reproducible.

P5  Improve observability, ergonomics, refactoring, documentation, or general architecture
    only when justified by a concrete current need and bounded by value.
```

Within a priority level, prefer the action with the best combination of:

```text
expected decision/strength value
critical-path impact
reversibility and containment
integrity and operational risk reduction
wall-clock impact
useful compute efficiency
ability to run independently in parallel
```

### 4.4 No progress exhaustion

Do **not** interpret “focus on progress” as permission to omit essential infrastructure.

Infrastructure is mandatory when it is needed to establish or preserve:

```text
clean V2 isolation
source and artifact provenance
legal/leakage-safe datasets and frozen evaluation boundaries
reproducible B0/champion reconstruction
safe artifact persistence and recovery
exactly-once/duplicate-effect protection for material operations
atomic promotion and rollback
cost/resource limits
production-valid ISS operation
stable host integration and release packaging
anti-Pseudo-V2 independence
FULL-AFK resilience and fault recovery
```

Do not overbuild speculative infrastructure before it is needed. Do not defer required infrastructure past the point where its absence risks evidence, reproducibility, safety, autonomy, or a required finish condition.

For infrastructure work, record the concrete blocked gate, failure mode, invariant, or required acceptance capability that justifies it.

---

## 5. Standard Control Loop

Apply this loop continuously. Do not turn it into repeated prose planning.

```text
INSPECT
→ establish minimum sufficient current evidence
→ classify active work
→ identify consequential gates and blockers
→ select highest-value safe action(s)
→ implement minimum necessary change
→ verify against explicit criteria
→ persist durable provenance/state
→ advance automatically
```

### 5.1 Minimum sufficient inspection

Inspect only what is needed to safely decide the next action, plus independent checks that can be bundled without delaying progress.

At resume or suspected drift, normally reconcile:

```text
V2 Git repository identity, branch, HEAD, remotes, worktree state
persistent V2 provenance/current manifests and artifact identities
active and recently interrupted workers/jobs/processes
storage health, important object existence, incomplete transfers, and material Git/Hetzner/RunPod hygiene risks
current champion/candidate/experiment/evaluation state
relevant ISS/external state before issuing related effects
resource/cost state when compute can be acquired or extended
```

Do not repeatedly conduct broad audits after the evidence needed for the next action is established.

### 5.2 Task contract

Before material work, define compactly:

```text
TASK_ID:
CURRENT_EVIDENCE:
GOAL_OR_HYPOTHESIS:
NEAREST_GATE:
SMALLEST_INTERVENTION:
INVARIANTS_TO_PRESERVE:
INPUT_IDENTITIES:
RESOURCE_BOUND:
SUCCESS_CRITERIA:
FAILURE_OR_STOP_CRITERIA:
VERIFICATION_METHOD:
PROVENANCE_OUTPUT:
NEXT_ON_SUCCESS:
NEXT_ON_FAILURE_OR_INCONCLUSIVE:
```

For a running task already launched, reconstruct this contract from durable evidence before materially changing it.

### 5.3 Completion discipline

Do not label a task complete because code exists, a command returned zero, a model trained, a process is running, or a dashboard shows green.

Completion requires the task’s stated verification and durable evidence.

After completion:

```text
record result;
record decision where relevant;
preserve negative/inconclusive evidence;
update compact current state;
advance to the next highest-value executable dependency.
```

---

## 6. Scientific Operating Rules

### 6.1 B0 and causal baselines

B0 is immutable.

Maintain reconstructable identities for the relevant source, weights, environment, inference, rules, runtime, and evaluation behavior.

Do not mutate B0 in place. Any challenger must have explicit lineage and a defined control.

The initial major learned milestone remains conceptually:

```text
B0 = frozen original SkatZero
B1 = identical frozen SkatZero cardplay + learned V2 bidding
```

At resume, do not assume any historical B1 name, candidate, model, or gate is current. Discover the actual accepted champion, active candidates, and latest valid decision.

### 6.2 Experimental discipline

Treat a model, data, search, belief, runtime, or integration change as a bounded treatment.

Where feasible:

```text
change one meaningful factor;
hold the relevant control constant;
preserve identities and seeds where applicable;
define decision criteria before evaluation;
measure deployment-relevant outcomes;
record ACCEPT, REJECT, or INCONCLUSIVE.
```

Do not promote on:

```text
training loss alone
offline accuracy alone
one proxy
one seed
one opponent
one noisy sample
unreconciled external gameplay
```

Use appropriate integrity, legality, leakage, stability, runtime, paired-gameplay, seat-balance, common-deal, statistical, contract-regression, and external-validation gates.

### 6.3 Weakness-driven selection

Select future treatments increasingly from measured weakness rather than fashionable architecture.

Relevant dimensions include:

```text
Suit / Grand / Null
declarer / defender
seat
bidding / declaration / discard / cardplay
opening lead / defender coordination
phase of game
belief / uncertainty / calibration
search disagreement
contract distribution
player/opponent strata
external failure type
latency / memory / runtime reliability
```

Disagreement is diagnostic, not proof of correctness.

### 6.4 Data, leakage, and evaluation boundaries

Never make training data authoritative without defensible identity for:

```text
source
parser
semantic validation
preprocessing
features
legal decision-time inputs
targets
filters
deduplication
quarantine policy
split membership
content/code hash
teacher identity/configuration where applicable
```

Maintain the conceptual trust levels:

```text
D0 EXISTS
D1 AUTHENTIC
D2 REPRODUCIBLE
D3 SEMANTICALLY_VALID
D4 EMPIRICALLY_USEFUL
```

D1–D3 may support bounded exploratory work only under the founding specification. Only controlled evidence establishes D4 usefulness.

Protect frozen train/validation/test and external holdout identities. External failure mining may create distinct, newly identified research cases, but must not train on frozen evaluation identities or leak their targets/identities into training selection.

### 6.5 Data sources and Legacy

Possible data sources include verified migrated historical corpus, independently acquired public archives, future external data, self-play, ISS gameplay, derived examples, and search-generated teacher data.

Never blindly concatenate sources.

Required conceptual flow:

```text
verified source
→ clean V2 parser
→ canonical representation
→ legality/scoring validation
→ semantic identity
→ deduplication
→ conflict handling
→ quarantine where required
→ frozen split membership
→ versioned dataset
```

Legacy assets remain read-only forensic candidates until they complete the founding salvage process:

```text
identify
→ hash
→ classify
→ establish provenance
→ verify authenticity and semantics
→ verify dependencies and data membership
→ verify train/eval separation and no leakage
→ verify reproducibility
→ test in isolation
→ ablate where required
→ explicitly allowlist
→ copy into clean V2 namespace
→ re-hash
→ register V2 provenance
```

No bulk V1 copy, V1 runtime dependency, inherited V1 authority, or historical label is permitted.

---

## 7. Infrastructure, Reliability, and Cost

### 7.1 Role separation

Preserve conceptual system roles unless newer verified architecture intentionally improves them:

```text
V2 Git                 authoritative source/configuration history
Persistent object store authoritative persistent data, models, provenance, evidence, releases
Compute/runtime        disposable/reconstructable execution, working cache, scratch
Secret systems         credentials and private runtime secrets
```

Do not commit secrets, credentials, large datasets, reconstructable caches, temporary checkpoints, private ISS credentials, or unintended model binaries.

Use normal Git transport where appropriate; do not reconstruct an existing repository history through file-by-file API commits.

### 7.2 Required reliability properties

Design and implement required workflows with:

```text
idempotency
content-addressed artifacts
atomic manifests
leases/heartbeats where useful
bounded retries
checkpoint/resume
partial-transfer detection
safe cleanup
transactional promotion/rollback
explicit resource and cost accounting
```

Recover automatically where possible from:

```text
worker restart or loss
GPU loss
network interruption
storage interruption
ISS disconnect
OOM
NaN/divergence
corrupt checkpoint
partial upload/download
storage pressure
duplicate dispatch
orphan/stale worker
lost update/concurrent write
resource leak
dependency failure
```

Do not import V1 recovery/control machinery wholesale. Use historical incidents as requirements and fault-injection cases.

### 7.3 Interruptions and material effects

A timeout, disconnect, lost response, or interrupted command means:

```text
OUTCOME = UNKNOWN
```

until verified.

Before repeating any material operation, determine whether it already occurred. This includes:

```text
Git commits/pushes
uploads/downloads
dataset generation
training launch/checkpoint
promotion/rollback
ISS/protocol action
worker provisioning
cleanup/deletion
release publication
```

Use actual state, logs, manifests, hashes, job identifiers, and external evidence. Avoid duplicate effects.

### 7.4 Resource and cost control

Track useful economics:

```text
GPU-hours
CPU-hours
compute cost
persistent storage cost
transfer
training/self-play/evaluation throughput
strength or information gain per useful compute
```

Use compute appropriate to the task. Do not constrain critical work because the current controller is small; do not permanently oversize infrastructure without evidence.

Keep permanently where appropriate:

```text
canonical data
accepted models
release artifacts
critical evidence
critical provenance
scientifically meaningful negative results
```

Bound retention of caches, reconstructable preprocessing, obsolete rejected checkpoints, scratch, and duplicates only after verifying that no unique evidence is lost.


### 7.5 Cross-system hygiene and discipline

Treat GitHub, Hetzner Object Storage, and RunPod as complementary authorities:

```text
GitHub   source, tests, reproducible configuration, compact provenance
Hetzner  curated durable data, models, required checkpoints, evidence, releases
RunPod   bounded active compute, staged inputs, working cache, scratch
```

Bind scientific results to exact source commits, artifact hashes, and evaluation evidence. Storage location does not confer scientific authority. No system should accumulate unclassified junk or be the sole copy of unique required work if transient. Apply hygiene at creation and transfer time, not just later cleanup. Do not interrupt sound work or let housekeeping displace a product/science gate.

### 7.6 GitHub hygiene and source-control discipline

Keep V2 Git a clean, authoritative source history, not an experiment ledger or runtime-state database.

- Commit coherent verified changes, not every edit or tiny fix. Group related work where sensible; do not automatically commit, push, merge, or open a PR after each test, checkpoint, or status event. Inspect the diff and run relevant focused checks.
- Synchronize verified source when another worker needs it, an experiment needs its exact source identity, or transient compute could lose the only copy. Do not sacrifice recoverability to minimize commit count. Preserve the commit graph and use normal Git transport.
- Create branches only for justified isolation, parallel work, risk, or review. Inspect existing branches/worktrees first. Prune only branches proven merged or superseded and without unique unmerged work, active jobs, PRs, worktrees, or release/evidence references. Old names alone do not establish disposability. Avoid gratuitous history rewrites.
- Git holds source, tests, schemas, reproducible configuration, build definitions, and compact provenance. Never commit secrets, credentials, large datasets, unintended model binaries, checkpoints, runtime logs/state, or caches. Reference bulky artifacts by hash and manifest.
- Push at meaningful integration/durability points; merge after applicable gates. Avoid unnecessary CI fan-out but not at the expense of durability. Keep ordinary CI small and deterministic; run expensive gameplay/training gates on dedicated compute.

Correct repository disorder when it risks correctness, reproducibility, recovery, collaboration, or cost; do not endlessly audit branches while useful SkatAI work is executable.

### 7.7 Hetzner Object Storage hygiene

Hetzner is curated, authoritative V2 artifact storage, not unlimited RunPod scratch. Capacity does not justify mirroring entire workspaces or retaining every intermediate.

- Before upload, assign each artifact family a producing job, purpose, content identity, storage class, and retention rule. Distinguish protected canonical data, frozen tests, accepted models/releases, critical evidence/provenance, and required rollback assets from active outputs, resumable checkpoints, quarantine, bounded experimental outputs, caches, and scratch.
- Stage uploads separately. Verify size, hash, schema, and manifest before publishing. Partial uploads are not valid artifacts; reconcile interrupted writes before retries or cleanup.
- Have workers fetch exact manifest-listed versions, not whole prefixes. Do not propagate stale, duplicate, or invalid objects. A canonical-looking name or prefix does not establish scientific authority.
- Protect canonical data, frozen evaluation identities, accepted models/releases, critical raw acceptance evidence, essential provenance, meaningful negative findings, and justified rollback checkpoints. Bound retention of obsolete rejected checkpoints, regenerable intermediates, duplicates, caches, and scratch; retain enough evidence to reconstruct negative conclusions.
- Before cleanup, inventory objects and check manifests, active jobs/leases, uniqueness, release/rollback references, and proven regenerability. Produce a dry-run list with reasons and expected savings. Delete only positively identified unreferenced disposable objects and verify the result. Never delete unique evidence on the basis of age, name, or prefix alone.

Do not let bucket reorganization displace science or product work.

### 7.8 RunPod pod and workspace hygiene

RunPod is bounded, reconstructable compute. Verify actual mounts, free bytes and inodes, container/overlay usage, workspace persistence, and large directories before budgeting or deleting. User-reported capacity is planning context, not a verified filesystem map; pod disk and mounted storage are not necessarily interchangeable.

- Estimate input, intermediates, checkpoints, outputs, and recovery headroom before large jobs. Fetch only manifest-listed artifacts; prefer bounded shards or streaming over unnecessary full copies. Do not launch work that cannot reasonably finish and recover within measured capacity.
- Use identifiable per-job workspaces. Separate active inputs and checkpoints from partial downloads, unpacked duplicates, caches, stale logs, temporary exports, and orphan outputs. Bound growth.
- Verify required outputs/checkpoints on Hetzner with hashes/manifests and synchronize unique verified source through Git before removing local copies or releasing the pod. A local checkpoint write alone is not durable recovery.
- After a job completes and its outputs are protected, remove disposable local material. Before consequential deletion check running processes, ownership, leases, references, and uniqueness; dry-run where appropriate. Do not interrupt a healthy run for cosmetic tidiness.
- Monitor bytes and inodes against headroom derived from actual job needs. Under pressure, pause new staging, identify growth, clear proven-disposable material, or safely reschedule. Never delete active checkpoints or unique evidence to relieve pressure.
- Verify that a clean worker can obtain source from V2 Git, required artifacts from Hetzner, and resume without undocumented files on an old pod or an active V1 dependency.

---

## 8. ISS and External Systems

Maintain one clean V2 ISS adapter and production-valid behavior for:

```text
login/session lifecycle
table/game lifecycle
bidding
declaration
discard
cardplay
reconnect
timeouts
unexpected messages
latency measurement
opponent identity
raw protocol evidence
game logging
crash recovery
duplicate-effect prevention
```

At resume, inspect relevant worker/process state, durable ISS gate state, active games, and mirrored evidence before issuing a related external effect.

ISS is primarily for:

```text
external generalization
real deployment validation
failure mining
exploitability detection
hard-state generation
runtime/protocol validation
```

Bulk RL normally belongs on controlled compute when it is cheaper, faster, and more reproducible.

Never optimize solely against one external opponent. Use diverse external opposition where technically and ethically appropriate.

Automatically classify useful external failures and turn them into reproducible controlled research cases without contaminating frozen external tests.


### 8.1 ISS games-per-hour and evidence transfer

During active ISS campaigns, maximize **valid completed games per wall-clock hour** subject to legal gameplay, protocol responsiveness, cost limits, and durable recovery. Game transitions are the critical path: do not put rclone, Hetzner uploads, full-directory scans, hashing of large backlogs, compression, or remote manifest writes synchronously between a finished game and the next table/game action. A running rclone process may still contend for CPU, disk, network, and file locks even when launched asynchronously; measure rather than assume it is harmless.

- Inspect the actual ISS worker, game-finalization path, rclone process tree, transfer triggers, local spool, and timing data before redesigning a system that may already have changed. Determine whether slowdown comes from blocking calls, resource contention, per-game process/listing overhead, protocol scheduling, or a different cause. Preserve valid current work.
- Finalize each game to a small, locally durable, immutable record with game ID, source/session identity, integrity information, and an enqueue marker; acknowledge only after the local durability condition is met. Decouple this from object-store acknowledgement. Immediately proceed with the next legal game when allowed. Never drop or fabricate results for throughput.
- Use a bounded, idempotent asynchronous outbox/queue with a separate transfer worker. Batch small closed-game records into content-addressed immutable segments where appropriate; upload only new manifest-listed artifacts. Coalesce per-game rclone invocations and avoid rescanning/mirroring whole directories. Use non-destructive copy semantics for authoritative evidence; a delete-capable mirror must not erase destination records when local spool rotates. Publish a segment manifest only after remote verification.
- Schedule or throttle transfers by measured ISS demand and spool pressure: favor genuine idle/off-peak windows, or low-priority background bandwidth/CPU/IO if it demonstrably does not affect gameplay. Limit transfer parallelism and I/O contention. Do not merely move a blocking upload into the *between-games* window and call it asynchronous. Avoid delaying the next game to clear a noncritical upload backlog.
- Monitor completed valid games/hour, game-finish-to-next-start delay, decision latency and timeouts (including tails), rclone CPU/disk/network use, outbox depth/age, free bytes/inodes, upload lag, and remote verification rate. Compare matched campaign windows before/after a bounded change; set deployment-appropriate throughput and maximum acceptable evidence lag/queue headroom from measurement, not arbitrary constants. Roll back any treatment that worsens gameplay or endangers evidence.
- Backpressure applies to transfer/storage safety, not indiscriminately to every game: a bounded local spool can absorb normal upload delay. If the spool approaches exhaustion, the worker may disappear before evidence can be copied, or a required durability bound cannot be met, protect data first: throttle or pause **new** games safely at a game boundary, retain active-game protocol correctness, reconcile and drain the outbox, then resume. Do not claim max throughput by risking unique ISS evidence trapped only on ephemeral pod storage.
- On crash/restart, reconcile local closed games, remote objects/manifests, and interrupted uploads by stable IDs/hashes before uploading again or issuing duplicate external game actions. Test disconnects, stalled object storage, upload retries, pod loss, and backlog pressure without losing unique evidence or duplicating games.

This is a product-science throughput requirement, not permission to discard provenance. Prefer a measured, minimal decoupling fix to a broad new orchestration subsystem. Note: `rclone copy` does not delete destination-only files; `rclone sync` can, so use delete-capable synchronization only with explicit scope and safeguards.

---

## 9. Product, Integration, and Release

Maintain one stable host-facing SkatAI interface, conceptually:

```text
load_model(...)
decide(observation)
```

or phase-specific equivalents:

```text
decide_bid(...)
choose_contract(...)
choose_discard(...)
play_card(...)
```

The host application must not depend on internal architecture, belief model, search implementation, specialist count, ensemble topology, or model family.

Accepted releases must support required gameplay phases and be validated for:

```text
legality and rule correctness
CPU runtime
GPU runtime where applicable
memory
p50/p95/p99 latency
crash behavior
host compatibility
release replacement without host-logic changes
```

Every accepted release binds appropriate identity for:

```text
release ID
source commit
parent lineage
model hashes
bidding/cardplay/belief/value identities
search configuration
rule-engine identity
runtime/deployment configuration
acceptance evidence
benchmark identity
build manifest
```

A release must be reproducible, versioned, and deployable from V2-authoritative artifacts.

---

## 10. Parallelism and Blockers

Do not globally serialize V2 work.

A blocker affects only the blocked task and real downstream dependencies. Continue independent work that is safe, bounded, and useful.

When blocked:

```text
identify exact blocker;
verify it;
persist it;
record required external action or condition;
set bounded retry/recheck policy where appropriate;
continue independent work;
do not repeatedly retry an impossible action;
do not fabricate completion.
```

Prefer maintaining both:

```text
one or more active paths toward the nearest scientific/product decision;
and only the infrastructure/reliability work needed to keep those paths valid.
```

Do not allow dashboards, planning, audits, or infrastructure polish to become substitutes for a live path to useful evidence.

---

## 11. Durable State and Machine-Readable Records

After a material advance, update compact durable V2 state. Do not turn the state record into an ever-growing narrative.

Use immutable evidence/ledgers for historical detail.

A current-state manifest should contain identities and current facts such as:

```json
{
  "captured_at": "ISO-8601 timestamp",
  "git": {
    "repository": "resolved repository identity",
    "branch": "current branch",
    "head": "verified commit",
    "remote_state": "verified push/ahead/behind state"
  },
  "champion": {
    "release_id": "or null",
    "artifact_ids": [],
    "acceptance_evidence": []
  },
  "candidates": [],
  "active_work": [],
  "datasets": [],
  "evaluations": [],
  "workers": [],
  "iss": {
    "state": "verified state",
    "active_games": [],
    "latest_evidence": []
  },
  "open_blockers": [],
  "nearest_consequential_gates": [],
  "current_highest_value_action": "",
  "important_artifact_hashes": [],
  "resource_state": {},
  "drift_or_reconciliation_notes": []
}
```

The exact schema may evolve, but it must be:

```text
compact
atomic
machine-readable
identity-based
updated after material change
traceable to immutable evidence
free of secrets
```

If live state is ahead of the manifest:

```text
verify the newer state
reconcile identities and effects
update durable state
continue from the newer verified position
```

---

## 12. Drift Detection and Cleanup

Continuously watch for drift, including:

```text
work disconnected from a consequential gate
unbounded experimentation
infrastructure built without a demonstrated requirement
repeated planning/auditing without a new decision
unproven datasets/models entering active science
leakage or holdout contamination risk
B0/champion mutation
V1 dependency or bulk inheritance
untracked long-running cost
missing provenance
non-atomic promotion or cleanup
host integration coupled to internal model design
dashboard/control-plane dependency
duplicate external effects
branch proliferation or unsafe pruning
unclassified or duplicate object-store artifacts and wasteful transfers
RunPod disk pressure or unique outputs stranded on a worker
ISS between-game transfer delays, throughput regression, or unsafe evidence backlog
```

When drift is found:

```text
1. State the precise defect and evidence.
2. Identify work/output that remains valid.
3. Preserve useful outputs, logs, and negative evidence.
4. Stop only the unsafe, invalid, wasteful, or unjustified portion.
5. Analyze downstream impact before changing shared artifacts/interfaces.
6. Apply the smallest reversible correction.
7. Verify against the relevant invariant or gate.
8. Persist the correction and resume productive work.
```

Do not erase history to make the project look cleaner. Do not preserve harmful complexity merely because it already exists.

---

## 13. Required Finish State

Do not declare the program complete after one candidate, one ISS campaign, one automated loop, or one product milestone.

Completion requires evidence that the work-prompt finish definition is satisfied, including:

```text
clean authoritative V2 Git and persistent storage
no hidden V1 dependency
reproducible canonical data and public-data processing
frozen leakage-safe evaluation boundaries
immutable reproducible B0
real bounded challenger decisions, including learned bidding gameplay decision
continued RL/self-play capability
belief/search/strength-treatment capability
population/league capability where required
production-valid ISS path and diverse external validation
external failure-mining loop
automated experiment selection/execution/evaluation
atomic promotion/rejection/rollback
recovery and budget enforcement
stable product interface and host integration
reproducible versioned release packaging
observability that is not operational authority
anti-Pseudo-V2 validation
sustained FULL-AFK fault-injection validation
ongoing autonomous challenger generation and testing
```

Finish is a demonstrated, repeatable capability—not an exhausted research space.

After the build finish definition is satisfied, operate the steady-state improvement loop:

```text
accepted champion
→ measure weakness
→ select highest-value bounded treatment
→ obtain/generate trustworthy data
→ train challenger
→ validate offline
→ run deployment-matched gameplay
→ validate externally where appropriate
→ ACCEPT / REJECT / INCONCLUSIVE
→ promote or retain incumbent
→ package/deploy accepted release
→ mine next weakness
→ repeat autonomously
```

---

## 14. Execution Command

Start now by establishing the minimum sufficient freshest state from:

```text
live physical/runtime systems
current V2 Git state
authoritative persistent artifacts and provenance
active/recent jobs and workers
current experiment/evaluation/champion state
relevant ISS/external-system state
resource/cost state where consequential
```

Then:

```text
classify active work as CONTINUE / CORRECT / CONCLUDE / SUSPEND / UNKNOWN;
protect valid work and unique evidence;
identify the nearest consequential gate(s);
identify the first real unresolved dependency for each valid path;
select the highest-value safe authorized action(s);
execute the minimum necessary bounded step;
verify it;
persist durable evidence and compact current state;
continue automatically.
```

Do not produce another roadmap when an executable step is available.

Do not restart valid work because this prompt arrived.

Do not continue invalid or counterproductive work because it is already running.

Do not regress to historical examples, phase numbers, named candidates, old hosts, old jobs, or old repositories merely because they appear in older prompts.

Continue until the verified finish state is achieved, then continue the steady-state autonomous improvement loop.
