# WORK PROMPT — SkatAI V2 End-to-End Build, Training, Autonomy, ISS and Product Integration

## Mission

Build **SkatAI V2** from the present state into a clean, reproducible, deployment-valid, continuously improving Skat AI system based initially on SkatZero.

The final system must:
- provide one integrated SkatAI interface for bidding, declaration, discard and cardplay;
- use the strongest empirically validated internal architecture, regardless of whether it contains one or several specialized models;
- exploit historical ISS/SkatGame data without information leakage or evaluation contamination;
- improve through supervised learning, self-play, reinforcement learning, belief/inference modelling, search and other evidence-backed methods;
- compete live on ISS against Kermit, Zoot, theCount, humans and other valid agents;
- use ISS results to mine weaknesses and create new training targets;
- autonomously train, evaluate, reject/promote, recover, checkpoint and continue without routine human intervention;
- operate FULL-AFK / 24/7/365 within explicit compute, storage and cost limits;
- integrate accepted releases into the user's Skat software through one stable deployment interface.

Primary optimization target:

> **reproducibly accepted deployment-valid Skat playing strength per useful compute and wall-clock time.**

## Binding specification

Before doing further implementation work, read and obey:

`SKATAI_V2_FOUNDING_SPECIFICATION.md`

Treat it as the binding founding specification.

Core invariant:

> **V2 inherits evidence, not architecture.**

Everything predating V2 is Legacy unless individually verified, content-addressed, allowlisted and migrated according to the specification.

Precedence:

```text
1. SKATAI_V2_FOUNDING_SPECIFICATION.md
2. Verified current physical/system state
3. Verified V2 provenance and experimental evidence
4. This execution prompt
5. Historical V1 evidence
```

Never let V1 labels such as `Final`, `Production`, `Champion`, `V2`, `Accepted`, or similar confer V2 authority.

# CURRENT STARTING STATE

Revalidate all of the following before relying on it.

## Legacy environment

V1 repository:

```text
https://github.com/foaryon/Skat
```

V1 RunPod tree:

```text
/workspace/skatai
```

Both remain Legacy / forensic reference. They must not become active V2 dependencies.

## Hetzner V2 storage

Primary V2 persistent object store:

```text
provider: Hetzner Object Storage
region:   fsn1
endpoint: https://fsn1.your-objectstorage.com
bucket:   skatai-v2
```

Credentials are supplied via standard S3/AWS environment variables. Never print or commit them.

Known V2 namespaces:

```text
canonical/
datasets/
evaluation/
evidence/
legacy-reference/
models/
provenance/
quarantine/
```

Hetzner is authoritative persistent V2 storage. RunPod is compute + working cache + scratch.

## Historical canonical corpus

Verified identity:

```text
records:          10,845,022
bytes:            13,077,428,492
ISS records:       8,692,393
SkatGame records:  2,152,629
SHA-256:
f429368a124a5bbd092c3c3d96169fdee909ce4c5a761083cbbf7e53b211d48a
```

Verified independent Hetzner copy:

```text
s3://skatai-v2/canonical/legacy-v1/full-2024-final/canonical.jsonl
```

Remote byte count and SHA-256 matched the source exactly.

## Public corpus refresh

A job was launched to independently fetch the July-2024 ISS and SkatGame SGF archives and stage them separately. Check its final state before use.

Never concatenate public archives directly with the Legacy canonical corpus. Required flow:

```text
verified legacy corpus
        +
independently downloaded public archives
        ↓
clean V2 parser
        ↓
canonical game identity
        ↓
deduplication
        ↓
conflict detection
        ↓
old / new / invalid classification
        ↓
V2 canonical corpus
```

## Fresh V2 repository

Current local source workspace:

```text
/workspace/skatai-v2
```

Known clean V2 commits already created:

```text
33ab7a5  Initialize clean-slate SkatAI V2
ff6c3fd  Track V2 data and model packages
fb5a00c  Freeze SkatZero V2-B0 identity
b3acb99  Record reproducible V2-B0 runtime
```

Revalidate current HEAD before acting.

A new GitHub V2 repository still needs to exist and become authoritative if that has not happened subsequently.

## B0 SkatZero baseline

Upstream:

```text
https://github.com/Jimboom7/SkatZero
```

Frozen upstream commit:

```text
1fe5cabbd5f9c3e77ab51714b0ac702e5a71e53b
```

Git tree:

```text
97092b62e5e8a768ebfc1e5945514598e1502404
```

Git archive SHA-256:

```text
40eff4a1f03aa0a0c0665a11b733505130a79e8ac646195c8dc51c61b6fb3f2d
```

The nine upstream pretrained D/G/N weights were individually hashed and published to:

```text
s3://skatai-v2/models/V2-B0/
```

Clean runtime reproduction:

```text
Python 3.11.13
torch 2.1.2+cpu
numpy 1.26.4
```

All nine models loaded as `DMCAgent`. The original API executed a cardplay smoke state successfully. This is runtime reproduction, not yet a full gameplay-strength reproduction.

## SentinelX / RunPod control path

Current RunPod control host:

```text
host_c8c6a6b0a7123805
```

The container has no systemd. RunPod's `/start.sh` invokes `/pre_start.sh`.

A neutral SentinelX replacement was staged under:

```text
/workspace/sentinelx-host/
```

with persistent identity/config copies and a watchdog supervisor. Make reconnection robust across actual pod recreation through an appropriate RunPod template, image, startup command or equivalent. Final V2 must not depend on `/workspace/skatai/ops/sentinelx`.

# EXECUTION PRINCIPLES

Work autonomously. Do not stop for routine confirmation when the next step is safe, reversible and clearly implied.

Use:

```text
inspect
→ establish evidence
→ implement minimum necessary change
→ verify
→ persist provenance
→ proceed
```

Prefer small, reversible, atomic, content-addressed changes.

Never delete unique evidence. Never promote on training loss or proxy metrics alone.

Scientific decisions are:

```text
ACCEPT
REJECT
INCONCLUSIVE
```

# PHASE 0 — REVALIDATE CURRENT STATE

Recheck:

```text
RunPod connectivity
Hetzner credentials/bucket health
V2 Git HEAD
background jobs
disk usage
public archive refresh
B0 objects/hashes
canonical corpus object/hash
SentinelX process ancestry
available CPU/GPU
```

Persist a current bootstrap-state manifest.

Exit: all later assumptions are freshly verified or explicitly marked uncertain.

# PHASE 1 — COMPLETE CLEAN V2 ISOLATION

Create a brand-new GitHub repository, preferably:

```text
foaryon/SkatAI-V2
```

Requirements:

```text
new repository
new Git history
main branch
no V1 fork ancestry
no imported V1 history
clean remote
minimal useful CI only
```

V2 must operate from:

```text
/workspace/skatai-v2
new V2 GitHub repository
s3://skatai-v2
```

Prove normal operation still works when V2 access to `/workspace/skatai` and `foaryon/Skat` is removed.

# PHASE 2 — SAFE V1 SALVAGE AND CLEANUP

Classify Legacy content as:

```text
VERIFIED_CANONICAL
VERIFIED_DERIVED
VERIFIED_REFERENCE
EMPIRICALLY_USEFUL
EVIDENCE_ONLY
QUARANTINED
REJECTED
UNKNOWN
DISPOSABLE_RECONSTRUCTABLE
```

Every useful V1 asset must pass the founding salvage gate.

High-priority candidate:

```text
/workspace/skatai/ops/precardplay-event-reconstruction-v1_1/
```

Recorded historical scale:

```text
101,544,070 candidate decisions
101,459,419 eligible decisions
69,762,716 bidding candidate decisions
69,696,014 bidding eligible decisions
217 shards
```

Either qualify it rigorously or reproduce equivalent data with clean V2 code.

Free expensive RunPod network storage only after protected migration and dependency checks.

# PHASE 3 — COMPLETE B0 SCIENTIFIC BASELINE

Complete reproducible B0 identity for:

```text
bidding
declaration
discard
Suit
Grand
Null
all roles/seats
latency
deterministic evaluation
scoring
resource profile
```

B0 is immutable.

A clean worker must reconstruct B0 from V2 Git + Hetzner without V1.

# PHASE 4 — BUILD V2 CANONICAL DATA SYSTEM

Implement independent V2 parsing and trusted game representation.

Capture:

```text
game ID
source lineage
date
players/ratings
initial hands
skat
bidding history
declarer
winning bid
contract
hand/pickup
discard
announcements
cardplay
tricks
points
result
score
parser version
raw hash
semantic identity
```

Replay accepted games through legality/scoring validation.

Explicitly quarantine malformed/incomplete/unsupported games.

Deduplicate across Legacy corpus, public ISS, public SkatGame and future imports.

Freeze train/validation/test partitions before training.

Maintain permanent holdouts for strong humans, Kermit, Zoot, theCount, other bots, recent periods, rare contracts and hard states.

# PHASE 5 — DATA AUTHENTICITY AND EXAMPLE GENERATION

Implement trust levels:

```text
D0 EXISTS
D1 AUTHENTIC
D2 REPRODUCIBLE
D3 SEMANTICALLY_VALID
D4 EMPIRICALLY_USEFUL
```

No training dataset is authoritative without:

```text
source identity
parser identity
preprocessing identity
feature contract
target contract
decision-time legality
filters
dedup
quarantine rules
split membership
content hash
code hash
teacher identity where applicable
```

Generate separate examples for bidding, declaration, discard, cardplay, value, belief and opponent modelling.

Targets may use hidden ground truth. Inputs may only use legally observable decision-time information.

# PHASE 6 — ANALYSE B0 WEAKNESSES

Measure B0 by:

```text
Suit / Grand / Null
declarer / defender
seat
early / middle / endgame
lead decisions
bidding
declaration
discard
overbid behavior
contract distribution
player-strength strata
Kermit/Zoot/theCount disagreement
strong-human disagreement
```

Agreement is diagnostic, not proof of strength.

Produce a weakness map that drives experiments.

# PHASE 7 — FIRST CAUSAL MILESTONE: B1 LEARNED BIDDING

Exact comparison:

```text
B0 = original frozen SkatZero
B1 = identical frozen SkatZero cardplay + learned V2 bidding
```

Only bidding changes.

Use legal decision-time bidding features.

Possible targets:

```text
next action
continue/pass probability
contract-conditioned value
win probability
expected score
overbid risk
contract likelihood
```

Evaluate offline diagnostics but promote only on deployment-valid gameplay using common deals, balanced seats and paired statistics.

Decision:

```text
ACCEPT
REJECT
INCONCLUSIVE
```

# PHASE 8 — CONTINUE SKATZERO-DERIVED RL

If bidding is accepted, integrate it into self-play/game generation and continue cardplay RL.

Compare against B0 and accepted B1.

Use population self-play with current champion, recent champions, historical champions, specialists, exploiters and fixed references.

Avoid private-convention collapse.

# PHASE 9 — BELIEF / HIDDEN-CARD MODELLING

Model:

```text
P(hidden deal | legal observation history)
```

Inputs can include own hand, bid history, contract, seat, played cards, trick sequence, follow-suit evidence, announcements and known skat information.

Enforce legal card allocations.

Evaluate calibration, hidden-card prediction, legal-world rate and downstream playing strength.

# PHASE 10 — SEARCH AND TACTICAL IMPROVEMENT

Test bounded candidates such as:

```text
PIMC
inference-weighted PIMC
belief-conditioned determinization
IS-MCTS
GO-MCTS
double-dummy/endgame solving
policy-guided search
value-guided search
search distillation
```

Measure strength per compute and latency.

Search survives only if deployment-valid gains justify runtime cost, or if it is useful as an offline teacher.

# PHASE 11 — BROAD EMPIRICAL STRENGTH LABORATORY

Test one bounded treatment at a time before combinations.

Candidate families include:

```text
transformers
residual MLPs
larger/smaller recurrent models
multi-task learning
policy/value heads
belief-conditioned policy
opponent modelling
league training
curriculum
hard-state replay
disagreement sampling
rare-contract oversampling
quality-weighted imitation
offline RL
search labels
distillation
ensembles
compression
quantization
runtime optimization
```

Keep a complete experiment ledger, including negative results.

# PHASE 12 — AUTOMATED SELF-PLAY AND HARD-STATE CURRICULUM

Prioritize states by:

```text
rarity
uncertainty
prediction error
search disagreement
opponent disagreement
strategic importance
external failures
contract underrepresentation
exploitability
```

Historical data remains an anchor against unrealistic self-play drift.

# PHASE 13 — ISS CLIENT AND REAL-WORLD LOOP

Implement a clean V2 ISS adapter.

Support:

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
game logging
opponent identity
latency
raw protocol evidence
```

Deploy against:

```text
Kermit
Zoot
theCount
other bots
humans where appropriate
```

ISS is primarily external generalization, hard-state generation, failure mining and exploitability detection; bulk RL normally stays local.

# PHASE 14 — EXTERNAL FAILURE MINING

Automatically classify ISS failures:

```text
bidding
overbid
underbid
declaration
discard
opening lead
defender coordination
Grand
Suit
Null
belief
search
value calibration
rule handling
runtime/timeout
```

Convert high-value failures into reproducible bounded cases without leaking frozen external tests into training.

# PHASE 15 — AUTONOMOUS EXPERIMENT CONTROLLER

Automate:

```text
select experiment
allocate resources
generate data
train
checkpoint/resume
validate
evaluate
gameplay gate
statistics
promote/reject
update population
archive evidence
cleanup disposable data
mine weaknesses
schedule next experiment
recover from failure
```

The controller must understand champion lineage, dataset identities, budget, prior negative results, weaknesses and deployment latency.

Do not begin with unrestricted self-modifying code. Prefer composable validated research blocks.

# PHASE 16 — PROMOTION SYSTEM

Never promote because loss/accuracy improved or one seed/opponent looked good.

Require appropriate gates:

```text
artifact integrity
model load
100% legal decisions
no leakage
numerical stability
dataset identity
runtime budget
paired gameplay
statistical stopping rule
contract-specific regression checks
external checks where required
release compatibility
```

Champion promotion and rollback are atomic.

# PHASE 17 — AFK RECOVERY

Automatically handle:

```text
worker restart
GPU loss
network interruption
Hetzner interruption
ISS disconnect
OOM
NaN/divergence
corrupt checkpoint
partial upload
storage pressure
duplicate dispatch
orphan/stale workers
lost update
concurrent write
resource leak
dependency failure
```

Use idempotent jobs, content-addressed artifacts, atomic manifests, leases/heartbeats where useful, bounded retry, checkpoint/resume and transactional promotion.

# PHASE 18 — RESOURCE AND COST CONTROL

Track:

```text
GPU-hours
CPU-hours
RunPod cost
storage cost
transfer
training throughput
self-play throughput
evaluation throughput
strength gain per compute
```

RunPod = compute.
Hetzner = persistent storage.

Keep permanent:

```text
canonical data
accepted models
release artifacts
critical evidence
critical provenance
```

Bound retention of rejected checkpoints and disposable caches.

# PHASE 19 — FULL-AFK ORCHESTRATION

Target behavior:

```text
start
→ restore deterministic state
→ identify useful work
→ acquire compute
→ stage data
→ train candidates
→ evaluate
→ promote/reject
→ update population
→ run ISS campaigns when scheduled
→ mine failures
→ schedule next bounded experiment
→ upload evidence
→ release compute
→ repeat
```

Routine crashes, rejects, promotions, rollbacks and ISS reconnects must not require a human.

# PHASE 20 — OBSERVABILITY

Build a new read-only V2 telemetry/dashboard layer showing:

```text
champion
active experiments
training progress
promotions/rejections
weakness metrics
ISS results
cost
worker health
storage
model lineage
dataset lineage
recent failures
```

Dashboard failure must never stop training, evaluation, ISS gameplay, recovery or promotion.

# PHASE 21 — CI/CD

Create minimal deterministic V2 CI for:

```text
lint/format where useful
unit tests
Skat-rule tests
schema tests
leakage tests
small model smoke test
artifact-manifest validation
```

Run expensive gameplay/training gates on dedicated compute only when meaningful.

# PHASE 22 — ONE INTEGRATED SKATAI PRODUCT INTERFACE

External interface should look conceptually like:

```text
load_model(...)
decide(observation)
```

or:

```text
decide_bid(...)
choose_contract(...)
choose_discard(...)
play_card(...)
```

The host Skat software must not know about internal belief models, search, ensembles or model heads.

The deployment artifact may internally contain multiple models/components, but externally it is one versioned SkatAI release.

# PHASE 23 — SKAT SOFTWARE INTEGRATION

Integrate accepted SkatAI into the user's Skat software.

Support all phases:

```text
bidding
game declaration
skat pickup
discard
cardplay
```

Potential runtime modes:

```text
Fast
Normal
Strong
Analysis
```

Validate legal actions, rules, CPU/GPU runtime, memory, p50/p95/p99 latency, crash behavior and compatibility.

Replacing the SkatAI release must not require changing host-game logic.

# PHASE 24 — RELEASE PACKAGING

Every accepted release binds:

```text
release ID
source commit
parent lineage
model hashes
bidding identity
cardplay identity
belief/value identity
search config
rule engine identity
runtime version
deployment config
acceptance evidence
benchmark identity
build manifest
```

Possible final artifact:

```text
SkatAI-vNNN.skatmodel
```

or an equivalent self-contained package/container.

# PHASE 25 — FINAL SCIENTIFIC ACCEPTANCE

Use large controlled comparisons with:

```text
common deals
seat balancing
paired statistics
fixed scoring/rules
fixed compute limits
frozen opponents
raw preserved results
confidence intervals
predefined stopping criteria
```

Compare where technically possible against:

```text
SkatZero B0
accepted V2 champions
Kermit
Zoot
theCount
other bots
diverse humans/ISS distribution
```

Do not optimize only for one opponent.

# PHASE 26 — ANTI-PSEUDO-V2 FINAL TEST

Remove active access to:

```text
/workspace/skatai
foaryon/Skat
```

Normal V2 operation must still support:

```text
data staging
training
evaluation
gameplay
ISS
promotion
recovery
dashboard
release build
Skat software inference
```

If not:

```text
FOUNDING STATUS = REJECTED — PSEUDO_V2 / V1.1
```

Fix the dependency.

# PHASE 27 — FULL-AFK END-TO-END ACCEPTANCE TEST

Run sustained unattended operation and inject failures:

```text
restart worker
terminate training worker
interrupt upload
remove compute temporarily
force resume
restart orchestrator
ISS reconnect
fill scratch near threshold
reject candidate
promote candidate
rollback candidate
```

Required end state:

```text
no routine human action
no lost provenance
no duplicate material effects
no corrupt promotion
no V1 dependency
no unique artifact trapped on worker
bounded spending
automatic recovery
continued useful scientific progress
```

# PHASE 28 — STEADY-STATE AUTONOMOUS IMPROVEMENT LOOP

Normal life becomes:

```text
accepted champion
→ measure weakness
→ choose highest-value treatment
→ obtain/generate data
→ train bounded candidate
→ offline validation
→ deployment-matched gameplay
→ ISS external validation where appropriate
→ ACCEPT / REJECT / INCONCLUSIVE
→ promote or retain incumbent
→ package/deploy
→ mine next weakness
→ repeat autonomously
```

SkatZero is the starting parent, not an architectural prison.

Every accepted change must have defensible evidence that it improves deployment-valid SkatAI or enables a subsequent accepted improvement.

# FINISH DEFINITION

Complete means all of the following are true:

```text
Clean V2 GitHub repository exists and is authoritative.
Clean V2 storage/provenance exists in Hetzner.
RunPod is compute, not authoritative storage.
SentinelX/worker bootstrap is independent of V1.
Historical corpus is migrated and verified.
Public ISS/SkatGame data is independently parsed/deduplicated.
Splits and leakage controls are frozen.
B0 is immutable and reproducible.
B1 learned bidding has received a real gameplay acceptance decision.
Continued RL/self-play exists.
Belief/search/other treatments can be tested causally.
Population/league training exists.
ISS client is production-valid.
Kermit, Zoot, theCount and other opponents are external benchmarks.
ISS failures feed a controlled weakness-mining loop.
Training/evaluation/promotion is automated.
Recovery is automatic and tested.
Budgets are enforced.
One stable SkatAI deployment interface exists.
Accepted SkatAI is integrated into the user's Skat software.
Release artifacts are reproducible and versioned.
Dashboard is observability-only.
V2 passes the anti-Pseudo-V2 test.
A sustained FULL-AFK test succeeds.
The system can keep generating and testing stronger challengers without routine human work.
```

Defining success condition:

> **SkatAI V2 autonomously and repeatedly produces statistically defensible, reproducible, deployment-valid improvements in real Skat playing strength, validates them locally and through ISS against diverse opposition including Kermit, Zoot and theCount, and deploys accepted versions through the same stable AI interface into the user's Skat software.**

## Operating instruction to the executing agent

Do not merely plan these phases. Execute them in dependency order.

Parallelize independent safe work.

Continue autonomously after each successful step.

At each phase:

```text
inspect
→ establish evidence
→ implement minimum necessary change
→ verify
→ persist provenance
→ proceed
```

When blocked, identify the exact external blocker and continue every independent workstream that remains possible.

Do not repeatedly ask the human to make ordinary technical decisions.

Do not rebuild solved infrastructure without evidence that it blocks AI progress.

Keep the majority of effort focused on:

```text
Skat understanding
data quality
bidding
cardplay
belief/inference
search
self-play
training
evaluation
real-world gameplay
model integration
```

Infrastructure exists to make the **SkatAI stronger**.
