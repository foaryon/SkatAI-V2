# SkatAI V2 — Clean-Slate Founding Specification

**Status:** FOUNDING SPECIFICATION  
**Generation:** V2  
**Purpose:** Clean scientific and engineering restart based on SkatZero  
**Legacy relationship:** V1 is forensic legacy only  
**Primary objective:** Maximum reproducibly accepted real-world Skat playing strength per useful compute and wall-clock time.

---

## 1. Founding Principle

SkatAI V2 is a **new project**.

It is **not**:

- a refactor of SkatAI V1;
- a new branch of the V1 repository;
- a cleaned-up V1 RunPod workspace;
- a continuation of V1 model lineage;
- a continuation of V1 autonomous-planner state;
- a continuation of V1 Dashboard;
- a continuation of V1 CI/CD;
- a continuation of V1 scientific authority;
- a continuation of V1 Master-Order state;
- a large-scale copy of V1 renamed to V2.

V1 is retained intact as:

> **LEGACY / FORENSIC EVIDENCE SOURCE**

V2 may inherit:

- independently verified evidence;
- independently verified canonical data;
- independently verified scientific findings;
- independently verified implementation ideas;
- useful negative results and failure knowledge;
- externally validated interfaces/contracts that remain genuinely useful.

V2 does **not** automatically inherit:

- architecture;
- source code;
- runtime state;
- models;
- checkpoints;
- training data;
- dashboards;
- workflows;
- recovery systems;
- scientific authorities;
- experiment state;
- conclusions;
- directory structures;
- Git history;
- deployment state.

**Core rule:**

> **V2 inherits evidence, not architecture.**

---

## 2. Primary Product and Optimization Target

The product is the **Skat AI**.

Infrastructure exists to make improvement of that AI:

- efficient;
- reproducible;
- autonomous;
- automated;
- resumable;
- observable;
- deployment-valid;
- eventually FULL-AFK / 24/7/365.

Infrastructure is a **means to an efficient scientific end**.

Infrastructure work MUST NOT become the dominant activity unless a measured infrastructure limitation materially blocks useful AI/data/training/evaluation/gameplay progress.

No recursive architecture-improvement loop is authorized merely because further architectural improvement is possible.

The project optimizes for:

> **accepted deployment-valid playing strength per useful GPU/CPU and wall-clock time**

while preserving:

- statistical validity;
- generalization;
- reproducibility;
- resumability;
- deployment fitness;
- provenance;
- valid prior work;
- infrastructure stability.

---

## 3. Scientific Baseline

The founding model baseline is:

> **SkatZero**

At V2 initialization, one exact SkatZero version MUST be frozen.

The freeze MUST bind:

- upstream repository;
- exact commit;
- source-tree hash;
- license;
- dependency lock/environment;
- pretrained model files;
- SHA-256 of every model;
- model architecture/configuration;
- inference implementation;
- original bidding implementation;
- training implementation;
- evaluation implementation;
- ISS integration;
- reproducible environment manifest.

The unmodified frozen system becomes:

> **V2-B0 — SKATZERO_BASELINE**

B0 is immutable.

All future V2 strength claims are measured relative to reproducible frozen baselines, beginning with B0.

V2 initially treats:

- **SkatZero cardplay/RL knowledge** as a valuable baseline;
- **SkatZero bidding** as the primary early improvement target.

---

## 4. Clean Project Boundary

V2 MUST begin with entirely new infrastructure identities.

| Domain | V2 rule |
|---|---|
| GitHub | New repository with new history |
| RunPod | New clean runtime/workspace |
| Persistent storage | New clean volume/data layout |
| Python/runtime | Newly reproducible environment |
| Secrets | Fresh explicit configuration |
| CI/CD | New minimal workflows |
| Dashboard | New implementation |
| Telemetry | New schema |
| Training orchestration | New minimal implementation |
| Recovery | New implementation |
| Releases | New pipeline |
| Scientific state | New |
| Experiment IDs | New namespace |
| Model lineage | New SkatZero-derived lineage |

### 4.1 Absolute anti-inheritance rule

The following are forbidden:

- recursive copy of the V1 workspace into V2;
- `rsync` of V1 as the foundation of V2;
- using V1 as the initial V2 Git working tree;
- creating V2 as a long-lived branch of the V1 repository;
- using a V1 fork as the actual V2 development base;
- bulk cherry-picking V1 history;
- copying V1 runtime state and then “cleaning it up”;
- using V1 Dashboard/CI/autonomy/recovery as a default V2 foundation;
- importing whole V1 subsystems merely because they already exist.

---

## 5. Anti-Pseudo-V2 / Anti-V1.1 Rule

This section is binding.

> **A V2 that materially depends on V1 runtime state, Git history, orchestration state, dashboard state, scientific authority, old recovery machinery, or bulk legacy subsystems is NOT V2.**

Such a system MUST be classified:

> **PSEUDO_V2 / V1.1 — REJECTED FOUNDATION**

V2 founding is invalid if any of the following is true:

- V2 cannot operate without the old `/workspace/skatai`;
- V2 requires the old `foaryon/Skat` repository as its active source tree;
- V2 consumes old planner/authority/state files as current authority;
- V2 imports old Dashboard/control-plane code wholesale;
- V2 starts from old training-state directories rather than clean V2 state;
- V2 inherits old branches/worktrees as its normal development process;
- V2 requires old recovery loops to stay alive;
- V2 bulk-imports legacy code before independently proving the need/value of each migrated component;
- V2 treats historical status labels such as `Champion`, `Accepted`, `V2`, `Final`, `Production`, or `Validated` as V2 evidence without re-verification.

**Name collision rule:**  
Any V1 artifact containing strings such as `v2`, `dashboard-v2`, `final`, `production`, or similar remains **V1 Legacy**. Its historical name confers **zero V2 authority**.

---

## 6. V1 Legacy Reference Zone — STRICT READ ONLY / REFERENCE ONLY

Everything in this section is:

> **V1_REFERENCE_ONLY**  
> **STRICT READ ONLY / REFERENCE ONLY**

These paths, repositories, branches and artifacts MAY be inspected, hashed, audited and used as forensic sources.

They MUST NOT be:

- executed as V2 code;
- imported as V2 runtime state;
- treated as V2 authority;
- copied wholesale;
- merged wholesale;
- recursively migrated;
- used as current training/evaluation truth without qualification;
- used as current deployment state;
- silently mounted/imported by V2.

### 6.1 V1 GitHub repository

**Legacy repository:**

```text
https://github.com/foaryon/Skat
```

Reference state recorded during V2 specification work:

```text
default branch: main
main HEAD: 589b60b5d70d45c0b3407310b9850d7de341fcf9
remote branches observed: 62
```

### 6.2 V1 GitHub branches — REFERENCE ONLY

```text
auditor-policy-binding-v1
backup/pre-gpu-execution-path-v2-0d7cfb4
bpcv1-gpu-execution-path-final-freeze-v1
bpcv1-gpu-execution-path-verify-v1
bpcv1-gpu-execution-path-verify-v2
bpcv1-gpu-execution-path-verify-v3
bpcv1-gpu-execution-path-verify-v4
bpcv1-gpu-execution-path-verify-v5
bpcv1-gpu-execution-path-verify-v6
bpcv1-gpu-execution-path-verify-v7
bpcv1-gpu-execution-path-verify-v8
bpcv1-gpu-execution-path-verify-v9
bpcv1-gpu-provider-scoped-attestation-candidate-v1
bpcv1-gpu-provider-scoped-attestation-verify-v1
bpcv1-gpu-provider-scoped-attestation-verify-v2
bpcv1-gpu-provider-scoped-attestation-verify-v3
bpcv1-gpu-relay-verify-observability-candidate-v1
bpcv1-gpu-relay-verify-observability-candidate-v2
bpcv1-gpu-relay-verify-observability-verify-v1
bpcv1-gpu-topology-diagnostic-one-shot-v1
bpcv1-provider-provenance-candidate-v1
bpcv1-rearm-provider-read-v1
bpcv1-terminal-rearm-lifecycle-candidate-v1
bpcv1-terminal-rearm-lifecycle-red-v1
bpcv1-terminal-rearm-lifecycle-red-v2
bpcv1-terminal-rearm-lifecycle-verify-v1
bpcv1-terminal-rearm-lifecycle-verify-v2
bpcv1-terminal-rearm-lifecycle-verify-v3
bpcv1-terminal-rearm-lifecycle-verify-v4
dashboard-access-redirect-diagnostic
dashboard-actions-cost-p0-20260921
dashboard-dynamic-transition-state
dashboard-live-fix-v1
dashboard-observability-v1
dashboard-p0-actions-steady-state-20260921
dashboard-p0-cloudflare-github-reconciler-20260921
dashboard-p0-persistent-github-observability-20260921
dashboard-p0-persistent-observability-20260921
dashboard-stale-relay-deploy-gate-v1-review
dashboard-stale-relay-deploy-gate-v1
dashboard-truth-binding-v1-20260920
dashboard-v21-postcutover-truth-fix
dashboard-v21-professional-presentation-hardening
dashboard-v22-postdeploy-polish
dashboard-v22-timeline-forensics-fix
diagnose-bpcv1-provider-auth-v4
feat/core-headless-foundation
fix/gpu-generic-billing-fallback
fix-bpcv1-provider-error-provenance-v1
h2i-v1.2-bootstrap
integrate-gpu-placement-gate-v1
main
openai-auditor-shadow-v1
phase4
planner-review-output-budget-v6
portable-secure-postcreate-v2
relay-atomic-publish-hardening
runpod-compute-lease-v1
runpod-relay-v1
skatai-bpcv1-stage-b-v1
skatai-portable-probe-v1
skatai-post-h-runpod-control-plane
```

These branch names are historical reference points only.

No branch above is a permitted V2 base branch.

---

## 7. V1 RunPod / Persistent Workspace Reference Zone

The current historical SkatAI tree is a grown runtime/data/experiment workspace, not a clean V2 foundation.

### 7.1 Legacy workspace root

```text
/workspace/skatai
```

This path is:

> **V1_REFERENCE_ONLY / STRICT READ ONLY**

It MUST NOT become the V2 workspace.

It MUST NOT be recursively copied into V2.

### 7.2 Related legacy infrastructure paths

```text
/workspace/sentinelx-cloud-core
/workspace/sentinelx-state
/workspace/skatai-cloudflare-setup
/workspace/skatai-cloudflare-telemetry
/workspace/skatai-cloudflare-transfer
```

These are also:

> **V1_REFERENCE_ONLY / STRICT READ ONLY**

V2 MUST NOT depend on them.

### 7.3 Important V1 project/reference paths

```text
/workspace/skatai/.github/
/workspace/skatai/.github/workflows/

/workspace/skatai/bakeoff/
/workspace/skatai/belief-pimc-pro6000/
/workspace/skatai/capacity-tournament-pro6000/

/workspace/skatai/code-9e35abc/
/workspace/skatai/prod-code-9e35abc/
/workspace/skatai/repo-9e35abc/

/workspace/skatai/runtime/
/workspace/skatai/runtime/xskat-5a14092/

/workspace/skatai/scaling-pro6000/
/workspace/skatai/selfplay-teacher-pro6000/

/workspace/skatai/ops/
```

### 7.4 Historical evaluation/training reference paths

```text
/workspace/skatai/capacity-tournament-pro6000/results-1m/
/workspace/skatai/capacity-tournament-pro6000/results-3m/
/workspace/skatai/capacity-tournament-pro6000/results-full/

/workspace/skatai/capacity-tournament-pro6000/human-frontier/
/workspace/skatai/capacity-tournament-pro6000/hyperparam-gate/
/workspace/skatai/capacity-tournament-pro6000/incumbent-gameplay-gate/
/workspace/skatai/capacity-tournament-pro6000/final-gameplay-gate/
/workspace/skatai/capacity-tournament-pro6000/generalization-cache-v1/

/workspace/skatai/capacity-tournament-pro6000/pairs/
/workspace/skatai/capacity-tournament-pro6000/evidence-gated/

/workspace/skatai/capacity-tournament-pro6000/human-capacity-1m-m4p97.json
/workspace/skatai/capacity-tournament-pro6000/human-capacity-1m-m26p1.json

/workspace/skatai/capacity-tournament-pro6000/stage1-selection.json
/workspace/skatai/capacity-tournament-pro6000/stage2-selection.json
/workspace/skatai/capacity-tournament-pro6000/stage3-finalists.json
```

All are historical evidence/reference only until independently qualified.

### 7.5 Important V1 ops paths

```text
/workspace/skatai/ops/dashboard-control-room-rebuild-20260920/
/workspace/skatai/ops/dashboard-control-room-visual-rebuild-20260921/
/workspace/skatai/ops/dashboard-correctness-fix/
/workspace/skatai/ops/dashboard-effective-baseline/
/workspace/skatai/ops/dashboard-final-closure-production-acceptance-20260922/
/workspace/skatai/ops/dashboard-final-closure-repo-20260922/
/workspace/skatai/ops/dashboard-final-production-verification-20260922/
/workspace/skatai/ops/dashboard-github-observability/
/workspace/skatai/ops/dashboard-p0/
/workspace/skatai/ops/dashboard-p0-current-audit/
/workspace/skatai/ops/dashboard-professionalization-20260912/
/workspace/skatai/ops/dashboard-total-forensic-audit-20260922/
/workspace/skatai/ops/dashboard-v2/
/workspace/skatai/ops/dashboard-v2-final-production-acceptance/
/workspace/skatai/ops/dashboard-v2-main-remote/
/workspace/skatai/ops/dashboard-v2-rebuild-20260919/
/workspace/skatai/ops/dashboard-v21-freeze-input/
/workspace/skatai/ops/dashboard-v21-postcutover-fix-local/
/workspace/skatai/ops/dashboard-v21-postcutover-fix-test2/
/workspace/skatai/ops/dashboard-v21-release-exact-2139d8d/
/workspace/skatai/ops/dashboard-v22-local-projection-test/
/workspace/skatai/ops/dashboard-v22-professional-live-fix-local/
/workspace/skatai/ops/dashboard-v22-timeline-targeted-test/

/workspace/skatai/ops/evidence-transition-controller/
/workspace/skatai/ops/github-actions-cost-p0/
/workspace/skatai/ops/h-to-i-gate/
/workspace/skatai/ops/main-executor/
/workspace/skatai/ops/offline-preparation/
/workspace/skatai/ops/offline-preparation-v1/
/workspace/skatai/ops/openai-auditor-shadow-v1/
/workspace/skatai/ops/parallel-science-factory/
/workspace/skatai/ops/phase-i-authorized/
/workspace/skatai/ops/phase-i-gate/

/workspace/skatai/ops/pimc-belief-capacity-value-v1/
/workspace/skatai/ops/pimc-belief-posterior-causal-value-v1/
/workspace/skatai/ops/pimc-distillation-value-v1/
/workspace/skatai/ops/pimc-engine-score-objective-value-v1/
/workspace/skatai/ops/pimc-odv-base-integration-value-v1/
/workspace/skatai/ops/pimc-odv-base-integration-value-v2/
/workspace/skatai/ops/pimc-override-world-consensus-value-v1/
/workspace/skatai/ops/pimc-search-horizon-value-v1/
/workspace/skatai/ops/pimc-search-value-distillation-value-v1/
/workspace/skatai/ops/pimc-trigger-breadth-value-v1/
/workspace/skatai/ops/pimc-world-count-value-v1/

/workspace/skatai/ops/portable-execution/
/workspace/skatai/ops/post-x9-reallocation/
/workspace/skatai/ops/precardplay-event-reconstruction-v1_1/
/workspace/skatai/ops/reciprocal-control-v1/

/workspace/skatai/ops/relay-freshness-recovery/
/workspace/skatai/ops/relay-secrets-v3/
/workspace/skatai/ops/relay-trust-rotation-backups/
/workspace/skatai/ops/repo-relay-github/
/workspace/skatai/ops/repo-relay-source/

/workspace/skatai/ops/runpod-control-plane/
/workspace/skatai/ops/runpod-recovery/

/workspace/skatai/ops/scm-evidence-hygiene-v1/
/workspace/skatai/ops/sentinelx/
/workspace/skatai/ops/skatai-autonomy-platform-v1/

/workspace/skatai/ops/skatzero-opponent-distribution-value-v1/
/workspace/skatai/ops/work-integration-acceptance-v1/
/workspace/skatai/ops/worktrees/
```

### 7.6 Explicit warning about misleading historical names

The following are still V1 Legacy despite containing “v2”:

```text
/workspace/skatai/ops/dashboard-v2/
/workspace/skatai/ops/dashboard-v2-final-production-acceptance/
/workspace/skatai/ops/dashboard-v2-main-remote/
/workspace/skatai/ops/dashboard-v2-rebuild-20260919/
```

They MUST NOT be confused with the new SkatAI V2 project.

---

## 8. Salvage Gate

No V1 asset crosses into active V2 directly.

Every candidate asset MUST pass a documented salvage process.

### 8.1 Required salvage sequence

```text
IDENTIFY
→ HASH
→ CLASSIFY
→ ESTABLISH PROVENANCE
→ VERIFY AUTHENTICITY
→ VERIFY SEMANTICS
→ VERIFY DEPENDENCIES
→ VERIFY DATA MEMBERSHIP
→ VERIFY TRAIN/EVAL SEPARATION
→ VERIFY NO INFORMATION LEAKAGE
→ VERIFY REPRODUCIBILITY
→ TEST IN ISOLATION
→ EMPIRICAL ABLATION WHERE REQUIRED
→ EXPLICITLY ALLOWLIST
→ COPY INTO CLEAN V2 NAMESPACE
→ RE-HASH
→ REGISTER V2 PROVENANCE
```

Skipping steps requires a written reason proving the omitted step is irrelevant.

### 8.2 Salvage classification

Every candidate receives one status:

- `VERIFIED_CANONICAL`
- `VERIFIED_DERIVED`
- `VERIFIED_REFERENCE`
- `EMPIRICALLY_USEFUL`
- `EVIDENCE_ONLY`
- `QUARANTINED`
- `REJECTED`
- `UNKNOWN`

`UNKNOWN` is preserved but MUST NOT be used.

`QUARANTINED` is preserved but MUST NOT be used for active V2 science.

### 8.3 No salvage by bulk copy

Forbidden:

```text
cp -a V1 V2
rsync V1 V2
git merge legacy-history
git cherry-pick huge legacy ranges
fork legacy and rename it V2
mount V1 state into V2 runtime
```

Migration MUST be content-addressed and allowlisted at asset/component level.

---

## 9. Canonical Historical Corpus Protection

The historical corpus is a high-value salvage candidate and MUST be protected before any cleanup.

Recorded V1 identity:

```text
records: 10,845,022
size: 13,077,428,492 bytes
ISS lineage: 8,692,393
SkatGame lineage: 2,152,629
SHA-256:
f429368a124a5bbd092c3c3d96169fdee909ce4c5a761083cbbf7e53b211d48a
```

Before any destructive cleanup of legacy storage:

1. independently calculate the source hash;
2. verify byte count;
3. verify record count;
4. verify source-lineage counts;
5. create at least one independent copy;
6. calculate the hash of the copied corpus;
7. require byte-identical SHA-256 equality;
8. persist immutable migration/source manifests.

No deletion or destructive reorganization may occur while the corpus has only one unverified copy.

---

## 10. Current Public Corpus Refresh

The old corpus MUST NOT simply be concatenated with newer ISS/SkatGame archives.

V2 MUST perform:

```text
legacy canonical corpus
        +
current external archives
        ↓
independent parse
        ↓
canonical game identity
        ↓
deduplication
        ↓
conflict detection
        ↓
old / new / invalid classification
        ↓
V2 CANONICAL CORPUS
```

No numerical claim of “new games added” is accepted before overlap/deduplication is measured.

---

## 11. Data Authenticity Standard

A file is not training data merely because it has a training-oriented name or extension.

Every training source MUST establish:

```text
raw source
→ source identity
→ parser identity
→ semantic validation
→ preprocessing identity
→ filters
→ quarantine/rejections
→ deduplication
→ feature construction
→ target construction
→ dataset membership
→ train/eval separation
→ final dataset hash
```

Required metadata include where applicable:

- source/game ID;
- source lineage;
- event identity;
- legal decision-time information;
- preprocessing code hash;
- transformation parameters;
- target origin;
- teacher identity;
- teacher configuration;
- quarantine reason;
- dataset membership;
- split identity;
- schema version.

Anything without a defensible chain is:

> **QUARANTINED_DATA**

and MUST NOT train V2.

---

## 12. Training-Data Trust Levels

### D0 — EXISTS
A file exists. No scientific authority.

### D1 — AUTHENTIC
Origin and identity established.

### D2 — REPRODUCIBLE
Can be regenerated from canonical input and exact transformation identity.

### D3 — SEMANTICALLY_VALID
Information legality, target semantics, deduplication and evaluation separation established.

### D4 — EMPIRICALLY_USEFUL
A controlled treatment-versus-control experiment demonstrates useful incremental value.

Only D1–D3 data may enter bounded exploratory work.

Only D4 may be described as:

> **proven useful V2 training data**

---

## 13. V1 Event Reconstruction

V1 recorded substantial reconstruction work, including approximately:

```text
candidate decisions: 101,544,070
eligible decisions: 101,459,419
bidding candidate decisions: 69,762,716
bidding eligible decisions: 69,696,014
shards: 217 / 217
```

Relevant reference path:

```text
/workspace/skatai/ops/precardplay-event-reconstruction-v1_1/
```

This is a high-priority **salvage candidate**, not automatically trusted V2 input.

Before V2 use:

- verify source corpus identity;
- verify transformation identity;
- verify event semantics;
- verify decision-time information legality;
- verify shard hashes/counts;
- verify quarantine/reject behavior;
- verify train/eval membership;
- independently reproduce representative samples;
- preferably parity-test a clean V2 reconstruction implementation.

Data value may be preserved.

Legacy implementation complexity need not be preserved.

---

## 14. m4p97 / m26p1 / PIMC Legacy Policy

All old m4p97, m26p1 and PIMC artifacts begin as:

> **QUARANTINED_REFERENCE**

They are NOT V2 parents, champions or teachers by default.

For every model require:

- exact model file;
- SHA-256;
- architecture;
- config identity;
- source code identity;
- dataset identity;
- selector;
- feature/target contract;
- seed;
- data seed;
- precision;
- optimizer;
- LR;
- weight decay;
- dropout;
- effective batch;
- sample/update count;
- checkpoint state;
- RNG/resume state;
- evaluation identity.

For PIMC additionally require:

- policy/model identity;
- PIMC code identity;
- world-generation semantics;
- belief/inference settings;
- trigger configuration;
- search budget;
- opponent identity;
- exact deal set;
- seat assignment;
- raw gameplay output;
- scoring implementation;
- reconstructed statistics.

If a historical strength claim cannot be fully reconstructed:

> **HISTORICAL_CLAIM_NOT_FULLY_REPROVEN**

This does not assert that the old claim was false.

It means V2 does not inherit the claim as proven.

---

## 15. Initial V2 Scientific Program

V2 begins narrow.

```text
B0
Frozen SkatZero
      ↓
V2 canonical historical corpus
      ↓
B1
learned bidding candidate
SkatZero cardplay unchanged
      ↓
offline evaluation
      ↓
deployment-valid gameplay
      ↓
ACCEPT / REJECT / INCONCLUSIVE
      ↓
improved bidding used in
future game generation
      ↓
continued SkatZero-derived RL
      ↓
B2
```

Initial hypothesis:

> Replacing SkatZero's weak conventional bidding with a learned, historically grounded bidding policy can improve bidding directly and may improve downstream self-play through more realistic game-state distributions.

---

## 16. Bidding-First Requirement

Bidding is the first major learned V2 extension.

The first clean causal comparison SHOULD be:

```text
B0 = frozen original SkatZero

B1 = frozen SkatZero cardplay
     + learned V2 bidding
```

Only the bidding component changes.

Bidding training MUST use decision-time legal information only.

Evaluation SHOULD include:

- decision/action prediction;
- calibration;
- pass/continue behavior;
- bid distribution;
- overbid rate;
- contract distribution;
- declarer frequency;
- game-type distribution;
- downstream gameplay.

Final acceptance is based on gameplay rather than imitation metrics alone.

---

## 17. Cardplay Data Policy

SkatZero cardplay MUST NOT be blindly overwritten by historical human behavior cloning.

Historical cardplay is initially:

> **candidate auxiliary information**

Possible uses include:

- state-distribution enrichment;
- bounded BC warm start;
- quality-stratified imitation;
- hard-state curriculum;
- disagreement sampling;
- PI/PIMC reanalysis;
- selective teacher targets.

Each treatment must earn its inclusion independently.

---

## 18. External Real-World Loop

The intended external improvement loop is:

```text
accepted SkatAI V2 candidate
        ↓
local deterministic validation
        ↓
deployment-matched gameplay
        ↓
ISS deployment
        ↓
Kermit / Zoot / theCount
and other valid opponents
        ↓
collect failures/disagreements
        ↓
attribute weakness
        ↓
construct bounded high-value training
        ↓
next candidate
```

ISS is primarily:

- external generalization evidence;
- real-world gameplay;
- implementation diversity;
- hard-state source;
- failure miner;
- exploitability detector.

Bulk RL should normally remain local when local simulation is cheaper, faster and more reproducible.

---

## 19. Evaluation Standard

Proxy metrics are diagnostic.

Final strength promotion requires deployment-valid gameplay.

Preferred comparison design:

- common deals;
- balanced seats;
- fixed opponent stack;
- identical resource constraints;
- fixed rules;
- exact scoring;
- paired statistics;
- predefined stopping rules;
- preserved raw results.

Only:

- `ACCEPT`
- `REJECT`
- `INCONCLUSIVE`

No winner is invented from statistical noise.

---

## 20. Clean Software Architecture

V2 begins with the smallest architecture reliably supporting:

```text
prepare data
→ train
→ evaluate
→ gameplay
→ decide
→ preserve evidence
→ repeat
```

Suggested conceptual packages:

```text
data/
game/
models/
training/
evaluation/
gameplay/
iss/
artifacts/
runtime/
tests/
```

No package exists merely because V1 had one.

---

## 21. Autonomy

The target remains:

> **autonomous + automated + FULL-AFK + 24/7/365**

but autonomy is built around a working scientific pipeline.

The initial orchestration provides only necessary functionality:

- deterministic work state;
- crash-safe persistence;
- exactly-once semantics for material effects;
- resume;
- bounded retry;
- compute lifecycle;
- storage management;
- checkpoint management;
- resource/cost accounting;
- health monitoring;
- automatic progression.

### Autonomy constraint

> No global serialization by default.

A blocked operation may block itself and true downstream dependencies, not unrelated productive work.

LLM/model reasoning is not the global clock.

No autonomous meta-planning loop may repeatedly consume resources without advancing useful V2 work.

---

## 22. Recovery

V2 MUST NOT import V1 recovery machinery wholesale.

V1 incidents become:

- requirements;
- fault-injection cases;
- regression tests.

Relevant failure classes include:

- duplicate dispatch;
- replay after restart;
- orphan worker;
- provider loss;
- OOM;
- storage exhaustion;
- partial/corrupted artifact;
- interrupted checkpoint;
- stale state;
- concurrent write;
- lost update;
- resource leak;
- dependency failure;
- dashboard outage;
- network outage.

For every relevant failure:

```text
reproduce
→ identify root cause
→ implement smallest robust protection
→ regression/fault test
→ verify
```

No repair-of-repair loop substitutes for root-cause removal.

---

## 23. Storage

V2 uses a **new clean storage namespace/volume**.

Recommended storage classes:

- `CANONICAL_DATA`
- `CHECKPOINT`
- `VALID_EVIDENCE`
- `ACTIVE_OUTPUT`
- `CACHE`
- `SCRATCH`
- `QUARANTINE`
- `LEGACY_REFERENCE`

Scratch/cache are disposable.

Canonical data and valid evidence are content-addressed and protected.

No V1 path is mounted as a writable V2 runtime dependency.

---

## 24. GitHub

V2 MUST use a brand-new repository and new Git history.

V1 repository:

```text
foaryon/Skat
```

remains Legacy.

V2 MUST NOT be:

- a V1 branch;
- a bulk V1 fork used as the real foundation;
- a history rewrite of V1;
- a cleanup commit series on V1.

Every salvaged component entering V2 must record:

- source V1 reference;
- source commit/file hash where available;
- review result;
- V2 destination;
- V2 tests;
- new V2 content hash.

Git is used for:

> source + configuration + compact provenance

not:

> runtime state + training logs + scheduler state + checkpoints.

---

## 25. Dashboard

The entire V1 Dashboard family is Legacy.

This includes all historical `dashboard-v2*` paths.

V2 Dashboard starts new and consumes a stable V2 telemetry API:

```text
V2 core
   ↓
read-only telemetry/state API
   ↓
V2 Dashboard
```

Dashboard MUST NOT be a control-plane dependency.

Dashboard failure MUST NOT stop:

- training;
- evaluation;
- gameplay;
- recovery;
- autonomous progression.

---

## 26. CI/CD

V1 GitHub Actions are Legacy.

V2 CI/CD starts new.

Default principles:

- fast deterministic tests for ordinary changes;
- targeted integration tests where needed;
- expensive tests only at meaningful gates;
- no permanent observability via hosted CI;
- no unnecessary fan-out;
- no workflow architecture whose operational cost exceeds its engineering value.

---

## 27. Release / Product Path

Research and product packaging remain separable.

Every accepted release binds:

- source commit;
- model hashes;
- bidding model;
- cardplay model(s);
- search configuration where applicable;
- game/rule engine;
- deployment configuration;
- acceptance evidence;
- build manifest.

Research may continue while an accepted release is packaged.

---

## 28. Legacy Knowledge Policy

Legacy information may be searched.

Legacy conclusions are not binding merely because they are documented.

For every old lesson:

1. determine whether the underlying problem exists in V2;
2. preserve the evidence;
3. choose the smallest V2-appropriate solution;
4. test that solution.

A V1 `LESSONS_LEARNED`, `FINAL`, `ACCEPTANCE`, `MASTER_ORDER`, `V2`, `PRODUCTION`, or similar artifact is historical evidence only.

---

## 29. Forbidden V2 Patterns

The following are forbidden unless independently justified by new V2 evidence:

- repeated architecture audits without a current blocker;
- repeated meta-planning;
- LLM review of deterministic transitions;
- multiple overlapping recovery controllers;
- Git branches as runtime state storage;
- Dashboard as control plane;
- broad automatic sweeps without evidence gating;
- unknown datasets entering training;
- checkpoint promotion from proxy metrics alone;
- silent dataset-lineage changes;
- copying entire V1 subsystems for convenience;
- preserving complexity because of sunk engineering effort;
- reusing a V1 status label as V2 scientific authority;
- building autonomy architecture faster than useful AI progress requires.

---

## 30. V2 Founding Acceptance Checklist

V2 founding is complete only when ALL applicable items below are true.

### Identity / isolation

- [ ] New V2 GitHub repository exists.
- [ ] New Git history begins at V2.
- [ ] V2 is not a branch/fork-as-foundation of V1.
- [ ] New clean runtime/workspace exists.
- [ ] New clean storage namespace/volume exists.
- [ ] No V1 runtime-state dependency exists.
- [ ] No V1 control-plane dependency exists.
- [ ] No V1 Dashboard dependency exists.
- [ ] No V1 CI/CD dependency exists.
- [ ] No V1 authority/state file controls V2.
- [ ] Legacy reference paths are read-only from the V2 perspective.

### SkatZero baseline

- [ ] Exact SkatZero upstream commit frozen.
- [ ] All baseline model hashes recorded.
- [ ] Environment reproducible.
- [ ] B0 local evaluation reproduced.
- [ ] Baseline gameplay identity preserved.

### Historical data

- [ ] V1 canonical corpus source hash independently verified.
- [ ] Record count independently verified.
- [ ] Source-lineage count independently verified.
- [ ] At least one independent verified copy exists.
- [ ] Copied corpus hash matches source byte-for-byte.
- [ ] No destructive legacy cleanup occurred before successful verification.
- [ ] New public archive refresh is staged separately.
- [ ] Deduplication/overlap methodology exists before merging sources.

### Derived/training data

- [ ] Data authenticity registry exists.
- [ ] Every imported dataset has provenance.
- [ ] Every imported dataset has a content hash.
- [ ] Train/eval membership is explicit.
- [ ] Leakage checks exist.
- [ ] Quarantine/reject semantics exist.
- [ ] Old training data starts quarantined until verified.
- [ ] m4p97 starts `QUARANTINED_REFERENCE`.
- [ ] m26p1 starts `QUARANTINED_REFERENCE`.
- [ ] PIMC claims start quarantined until raw evidence and identity are verified.

### Software/science loop

- [ ] Minimal V2 data path works.
- [ ] Minimal V2 training path works.
- [ ] Minimal V2 evaluation path works.
- [ ] Minimal V2 gameplay path works.
- [ ] Evidence is content-addressed.
- [ ] Checkpoint/resume behavior is verified.
- [ ] One bounded causal experiment can run end-to-end.
- [ ] Bidding-first B1 experiment can be specified without V1 authority.

### Anti-Pseudo-V2 test

- [ ] Removing access to `/workspace/skatai` does not break normal V2 operation after approved assets have been migrated.
- [ ] Removing access to `foaryon/Skat` does not break normal V2 operation after approved assets have been migrated.
- [ ] No active V2 module imports code directly from V1 paths.
- [ ] No active V2 service reads V1 runtime state as current state.
- [ ] No V1 subsystem was bulk-copied as the V2 foundation.
- [ ] An independent reviewer would classify the system as a new generation, not V1.1.

If the anti-Pseudo-V2 test fails:

> **FOUNDING STATUS = REJECTED — PSEUDO_V2 / V1.1**

---

## 31. First Science Milestone

> **SKATZERO + LEARNED BIDDING**

All SkatZero cardplay components otherwise remain frozen for the initial causal test.

Success requires deployment-valid improvement without unacceptable regression.

Positive offline bidding metrics alone are insufficient.

---

## 32. Second Science Milestone

After Bidding acceptance:

- integrate accepted learned bidding into realistic game generation;
- continue SkatZero-derived RL;
- compare against B0 and accepted bidding-only baseline;
- test whether better bidding improves downstream learned cardplay distribution.

---

## 33. Third Science Milestone

Only after the clean V2 line exists may verified Legacy knowledge sources compete for admission.

Preferred design:

```text
current accepted V2
vs
current accepted V2 + legacy source X
```

Outcomes:

- `USE`
- `REJECT`
- `INCONCLUSIVE`

No bulk Legacy-data mixture is automatically authorized.

---

## 34. Final North Star

The intended steady-state loop is:

```text
accepted SkatAI
      ↓
identify real weakness
      ↓
obtain/generate high-value data
      ↓
train bounded candidate
      ↓
offline validation
      ↓
deployment-matched gameplay
      ↓
ISS / external validation
      ↓
statistical decision
      ↓
accept or retain incumbent
      ↓
mine next weakness
      ↓
repeat autonomously
```

The success measure is not architecture completeness.

The success measure is:

> **Does SkatAI V2 continue to produce reproducible, deployment-valid improvements in real Skat playing strength without requiring routine human intervention?**

---

## 35. Immutable Founding Rule

> **Everything that predates the creation of the V2 project is Legacy.**

That includes:

- this V1 project;
- all V1 repositories;
- all V1 branches;
- all V1 RunPod storage;
- all V1 code;
- all V1 models;
- all V1 checkpoints;
- all V1 training data;
- all V1 science results;
- all V1 dashboards;
- all V1 orchestration;
- all V1 CI/CD;
- all V1 prompts;
- all V1 Master Orders;
- all V1 authorities;
- all V1 lessons-learned documents;
- all V1 deployment state;
- all V1 artifacts whose names already contain `V2`.

Legacy may inform V2.

Legacy may supply individually verified, content-addressed, explicitly allowlisted assets.

Legacy never controls V2.

> **SkatAI V2 starts clean. No Pseudo-V2. No V1.1.**
