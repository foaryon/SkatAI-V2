# SkatAI V2 — MAIN Current Execution Brief

Captured: 2026-09-25 UTC.

This file is recovery context, not a replacement specification. SKATAI_V2_FOUNDING_SPECIFICATION.md, SKATAI_V2_WORK_PROMPT.md, and SKATAI_V2_MASTER_CONTINUE_MERGED.md remain authoritative. On resume, verify any state that can have changed. Do not return to an older checkpoint merely because it is named here.

## Mission / final target

Carry SkatAI V2 to a clean, reproducible, deployment-valid and continuously improving Skat system: trustworthy data -> training -> challenger -> controlled evaluation -> promotion/rejection -> release -> stable host integration, with FULL-AFK recovery, scientific gates, external validation/failure mining, and ongoing autonomous improvement.

ISS throughput is one product-science workstream, not the whole project. A healthy independently supervised ISS treatment is an external dependency, not permission to open arbitrary parallel work. MAIN follows only the authenticated execution lock supplied by the controller and never spends model tokens live-polling healthy games.

## Verified architecture milestone

Multi-table/throughput runtime work is merged into authoritative main; merge commit e7a8fae89baa9744d9755553f2c27d68c4d21262.

Implemented and tested capabilities include:
- crash-safe per-table slot/active-game authority and per-game arm routing;
- async decision scheduling so one slow table does not block the ISS network loop;
- stale-result revalidation before any material ISS send;
- persistent warm SkatZero worker pool with request-local mutable game state;
- one Torch thread per warm worker by default from measured cardplay throughput;
- B0 max-bid in-flight deduplication and safe off-turn prefetch;
- locally durable game closure plus bounded asynchronous Hetzner write-behind/backpressure;
- restart/departure guards for unknown table/effect outcomes;
- distinct candidate deployment epochs and fail-closed cutover preflight;
- machine-readable throughput/latency/integrity metrics;
- independent fail-closed frozen-R9 supervisor;
- real worker-crash and storage-outage/backpressure fault validation.

The merged main test suite passed 512 tests after the throughput integration.

## Frozen R9 — verify before acting

Frozen source remains c77b401f3665ebd64c7c62491e122d8ccdfd22f1 in /workspace/skatai-v2-wt-r9-ouvert-outbound.

Snapshot at this brief:
- scored: B0 189, B1 189;
- active games: 1;
- pending external effects: 0;
- ledger status counts: INFRA_FAILURE 4, PROTOCOL_FAILURE 4, SCORED 378;
- independent supervisor state: RUNNING.

The first strength look remains closed until the preregistered 300 scored games per arm boundary. Do not modify the frozen R9 treatment for throughput. Do not run heavy competing benchmarks on the same CPU while R9 is scientifically active.

The supervisor is intentionally independent from MAIN. If the worker disappears, automatic restart is allowed only at a proven clean boundary with no active game and no pending/unknown material effect. Otherwise fail closed and reconcile.

## Frozen R9 throughput baseline

Authoritative evidence: provenance/ISS_R9_THROUGHPUT_BASELINE_20260925.json.

At the frozen baseline snapshot:
- recent window: 50 scored games;
- recent valid throughput: 14.487 games/hour;
- completion-gap median: 190.933 s;
- completion-gap p95: 687.906 s;
- duplicate game IDs: 0;
- effect chain valid: true;
- pending effects at snapshot: 0;
- BID p95: 34548.562 ms;
- DECLARATION p95: 64005.884 ms;
- PLAY_CARD median: 1568.939 ms.

Legacy R9 predates per-game mirror receipts; absence of receipt tracking must not be misreported as missing mirrored evidence.

## Throughput findings that should not be rediscovered repeatedly

1. Frozen R9 had synchronous remote mirroring/readback on the game-transition critical path.
2. Slow first decisions are real. Frozen B0 bidding can take tens of seconds because its sequential simulation loop is expensive; first declaration also has large tails.
3. Repeated SkatZero process/model startup was a major avoidable cost. Warm CARDPLAY probes preserved the final action while substantially reducing repeated latency.
4. Torch intra-process fan-out was counterproductive on the current 8-vCPU pod; bounded inter-process warm workers performed better.
5. Frozen B0 bidding is stateful/sequential: shuffled Skat combinations, accumulated estimates, and later early-skip behavior depend on earlier iterations. Do not naively split the 231-step loop across workers and call it equivalent. Use safe prefetch/dedup for the deployment-preserving path; a changed bidding algorithm is a new experiment.
6. Multi-table support exists at the ISS session/state layer; the old single-active-game restriction was in our gate runtime, not a reason to keep production single-table forever.
7. MAIN/control-plane lifecycle must not own scientifically relevant external workers.

## Post-R9 deployment ladder

Do not start the candidate while frozen R9 is still running or has unresolved authority. scripts/preflight_iss_throughput_cutover.py is intentionally fail-closed.

At the first clean post-R9 boundary:
1. Start a new deployment epoch with 1 table / 1 warm worker using the new async/write-behind runtime. This isolates runtime improvements from table concurrency.
2. Compare against the frozen baseline using scripts/collect_iss_throughput_metrics.py.
3. If integrity/recovery gates pass and valid games/hour improves, move to a separate 2 table / 2 worker epoch.
4. Only then test a separate 4 table / 4 worker epoch.
5. Do not mix runtime roots/evidence between ladder steps. Preflight must reject unresolved earlier candidate epochs.
6. Choose the final table/worker count from measured end-to-end games/hour, recent p50/p95/p99 decision latency, protocol/infra failures, duplicate effects/game IDs, CPU/memory/PSI, outbox depth/age, mirror lag and recovery behavior — not from table count alone.
7. Roll back or stop widening concurrency if integrity, tails, ISS responsiveness, evidence lag, or games/hour regress.

The target is the highest sustainable valid, deployment-safe games/hour, not a fixed number of tables.

## Other current constraints

- MAIN pod class is 8 vCPU / 16 GB.
- Network-drive capacity has been near its limit (approximately 119/120 GB signal). Avoid broad repeated scans and large local staging until headroom is freshly verified. Hetzner remains the durable bulk/evidence target; reclaim local data only after identity/hash/readback verification.
- The ISS password remains file-backed. Do not put its value into prompts, logs, Git, browser state, or child environments when a private file reference suffices.
- The independent ISS runtime metadata contract is /workspace/skatai-v2-runtime/iss/iss-runtime.env and contains only non-secret connection metadata plus ISS_PASSWORD_FILE.
- Big Prompt SHA-256 after the Multi-Table update: 2eb047d6ddbf19ed541e0118cb87c4147c75acd2cf54e2925811e7e8883052a4.

## Cost-discipline execution governance

The 2026-09-25 MAIN cost incident established that idle-driven continuation and commit-sensitive progress are unacceptable. MAIN now operates only under the trusted execution governor, an authenticated root-owned execution lock, and a finite root-owned work permit. Exactly one PRIMARY gate is executable; a SECONDARY is allowed only when explicitly named and PRIMARY is externally blocked/waiting. Idle, commits, test passes, documentation, general audits and cleanup do not authorize another autonomous turn. Every turn must produce a nonce-bound outcome through the controller's record_turn_outcome function, and only material gate progress can authorize a follow-up within the same permit. The controller remains fail-closed until explicit reenable conditions are satisfied.

## Resume behavior

On MAIN resume:
- inspect this brief once, then read the execution lock and verify only state relevant to its PRIMARY gate;
- do not redo already-validated work merely because this brief mentions it;
- protect frozen R9 under its independent supervisor and react only to a real gate transition/anomaly;
- do not open independent science/product work unless it is the locked PRIMARY or an explicitly authorized SECONDARY;
- after a terminal PRIMARY decision, stop; a new PRIMARY requires a newly authenticated operator-issued permit before changing workstreams;
- keep the overall SkatAI V2 final product mission above infrastructure or throughput subprojects.
