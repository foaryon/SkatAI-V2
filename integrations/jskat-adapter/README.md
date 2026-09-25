# SkatAI V2 JSkat Adapter

This module is the host-side Java bridge between pinned JSkat and the stable SkatAI V2 JSON-lines product boundary.

## Boundaries

- JSkat remains an external host dependency; it is not vendored here.
- The adapter class is `org.skatai.v2.jskat.SkatAIJSkatPlayer`.
- Python model/runtime internals remain behind `skatai.runtime.host_service`.
- The adapter validates release identity, response schema, legal actions, pickup-plan identity, and callback-specific state before returning a JSkat move.
- Bidding, declaration/pickup, discard, contract mapping, and cardplay are mapped.
- Contra/Re and Ramsch remain outside the current V2 product phase surface.

Pinned JSkat source used for this integration gate:

```text
9ef3cd6a081c54cc84ed61378ed3879992246ae7
```

## Build

JDK 25 is the deployment-matched JSkat CI runtime used for this gate.

```bash
./gradlew -p integrations/jskat-adapter \
  -PjskatBaseJar=/path/to/jskat-base.jar \
  test jar
```

The produced `skatai-jskat-adapter.jar` includes its Jackson runtime dependencies but not JSkat.

## Runtime

The adapter launches the Python host service with these environment variables:

```text
SKATAI_V2_PYTHON=/path/to/python
SKATAI_V2_PACKAGE=/path/to/release.skatmodel
SKATAI_V2_MATERIALIZE_TO=/path/to/materialized-release
SKATAI_V2_RELEASE_ID=<expected release id>
SKATAI_V2_HOST_TIMEOUT_MS=30000
SKATAI_V2_PYTHONPATH=/path/to/skatai-v2/src   # source-worktree/dev only
```

`SKATAI_V2_RELEASE_ID` is optional but should be set in deployment so a wrong release fails closed. The timeout is a safety ceiling, not an acceptance latency target.

The B0 host service uses one persistent SkatZero worker by default. On the 2026-09-25 main pod, the verified B0 package showed about 5.3 s for the first BID computation and microsecond-level reuse of the exact max-bid cache; the prior cold-process path was roughly 99 s under concurrent R9 load. Final product latency acceptance remains a separate gate and should use the accepted learned bidding release rather than treating this B0 number as the target.

## JSkat application integration

Pinned JSkat can instantiate arbitrary player classes with `Class.forName()`, but its UI resolver is a fixed list. The pinned-source patch under `patches/` keeps SkatAI optional and fail-closed:

- the resolver exposes **SkatAI** only when `org.skatai.v2.jskat.SkatAIJSkatPlayer` is actually loadable;
- the normal series-start UI renders that optional player as **SkatAI**;
- `app` accepts an explicit `-PskataiAdapterJar=/path/to/skatai-jskat-adapter.jar` runtime dependency, so `installDist` and the application runtime include the adapter without vendoring it into JSkat;
- `SkatTable.removePlayers()` closes players that implement `AutoCloseable` before clearing the table, preventing a previous SkatAI Python/warm-worker process from leaking when a new series replaces the players.

The patch does not change JSkat rules, bidding order, card legality, scoring, or game-play decisions.

A deployment-matched local distribution can be built with:

```bash
git -C /path/to/JSkat apply --check \
  /path/to/SkatAI-V2/integrations/jskat-adapter/patches/jskat-skatai-player.patch

git -C /path/to/JSkat apply \
  /path/to/SkatAI-V2/integrations/jskat-adapter/patches/jskat-skatai-player.patch

/path/to/JSkat/gradlew -p /path/to/JSkat :app:installDist \
  -PskataiAdapterJar=/path/to/skatai-jskat-adapter.jar
```

For source-worktree testing, the Python SkatAI V2 runtime is supplied with `SKATAI_V2_PYTHONPATH`. A final product distribution should install/package the matching Python runtime rather than depend on an arbitrary development worktree.

## Verification layers

Normal module tests use a deterministic fake host and cover:

- the special forehand self-offer at 18;
- rearhand reconstruction of the first-duel winner;
- the JSkat repeated-264 callback guard;
- atomic pickup-plan caching across discard and announcement;
- all reviewed contract token mappings in both directions;
- canonical Java reproduction of Python request/position identities;
- cardplay history and exact JSkat legal-card mapping;
- completed-trick point attribution from JSkat winner state;
- defender reconstruction of JSkat's unreported solo-forehand 18;
- declarer visibility of only the two actually buried cards;
- fail-closed response schema, release, request identity, position identity and legal-action checks.

The opt-in `JsonLineHostClientIntegrationTest` uses the configured real `.skatmodel` and exercises BID, pickup declaration, discard, and cardplay through Java -> JSONL host -> validated release -> persistent SkatZero worker -> Java. It is skipped when the deployment environment variables are absent.

For pinned JSkat commit `9ef3cd6a081c54cc84ed61378ed3879992246ae7`, the patch is separately checked with its resolver regression, AutoCloseable table-lifecycle regression, GUI Kotlin compilation, `app:installDist`, runtime class discovery, reflective player construction, and a post-close orphan-process check.

This integration is staged product work. It does not promote B0, select the eventual champion, or by itself constitute the final Windows/JSkat product distribution.
