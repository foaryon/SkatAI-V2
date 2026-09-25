#!/usr/bin/env bash
set -euo pipefail
umask 027

ROOT="${ISS_THROUGHPUT_ROOT:-/workspace/skatai-v2-iss-throughput-multitable}"
RUNTIME="${ISS_THROUGHPUT_RUNTIME_ROOT:-/workspace/skatai-v2-runtime/iss/throughput-candidate-v1}"
R9_RUNTIME="${ISS_R9_RUNTIME_ROOT:-/workspace/skatai-v2-runtime/iss/external-gate-r9}"
ISS_PASSWORD_FILE="${ISS_PASSWORD_FILE:-/run/skatai-v2-secrets/iss_password}"
EXPECTED="${ISS_THROUGHPUT_EXPECTED_COMMIT:?set ISS_THROUGHPUT_EXPECTED_COMMIT to the pinned candidate commit}"
TABLES="${ISS_GATE_TABLES:-2}"
WORKERS="${ISS_GATE_INFERENCE_WORKERS:-2}"
LOG="$RUNTIME/launcher.log"
PREFLIGHT="$RUNTIME/preflight.json"
LOCK="$RUNTIME/.launch.lock"

fail() {
  mkdir -p "$RUNTIME"
  printf '%s ISS_THROUGHPUT_LAUNCH_FAIL %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG"
  printf '%s\n' "$*" >&2
  exit 1
}

case "$TABLES" in
  2|4) ;;
  *) fail "tables-must-be-2-or-4:$TABLES" ;;
esac
case "$WORKERS" in
  1|2|3|4) ;;
  *) fail "workers-out-of-range:$WORKERS" ;;
esac
[ "$WORKERS" -le "$TABLES" ] || fail "workers-gt-tables:$WORKERS>$TABLES"

mkdir -p "$RUNTIME"
exec 9>"$LOCK"
flock -n 9 || fail "candidate-launch-lock-busy"

[ -d "$ROOT/.git" ] || [ -f "$ROOT/.git" ] || fail "missing-candidate-worktree"
head="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
[ "$head" = "$EXPECTED" ] || fail "candidate-head-mismatch:$head"
[ -z "$(git -C "$ROOT" status --porcelain)" ] || fail "candidate-worktree-dirty"
[ -s "$ISS_PASSWORD_FILE" ] || fail "iss-password-file-unavailable"

export PYTHONPATH="$ROOT/src"
python3 "$ROOT/scripts/preflight_iss_throughput_cutover.py"   --repo "$ROOT"   --expected-commit "$EXPECTED"   --r9-runtime "$R9_RUNTIME"   --candidate-runtime "$RUNTIME"   --iss-password-file "$ISS_PASSWORD_FILE"   --output "$PREFLIGHT"   || fail "preflight-blocked"

# Rehydrate immutable local model/runtime assets only after cutover safety is
# proven. Never mutate the frozen live R9 process to prepare this candidate.
export UV_CACHE_DIR=/tmp/skatai-v2-uv-cache
"$ROOT/scripts/bootstrap-local-b1-pregate.sh" >>"$LOG" 2>&1   || fail "bootstrap-failed"

export SKATAI_V2_ROOT="$ROOT"
export ISS_GATE_RUNTIME_ROOT="$RUNTIME"
export PYTHONPATH="$ROOT/src"
export SKATZERO_ROOT=/tmp/skatai-v2-b0
export SKATZERO_PYTHON=/tmp/skatai-v2-b0-venv/bin/python
export B1_MODEL=/tmp/skatai-v2-b1-linearish-full-v1/model.pt

export ISS_GATE_TABLES="$TABLES"
export ISS_GATE_INFERENCE_WORKERS="$WORKERS"
export ISS_GATE_WARM_SKATZERO=true
export ISS_GATE_ASYNC_DECISIONS=true

# Worker-level parallelism is intentionally inter-process. Benchmarks on the
# 8-vCPU MAIN showed Torch intra-op fan-out >1 sharply reduced throughput.
export SKATZERO_TORCH_THREADS=1
export SKATZERO_TORCH_INTEROP_THREADS=1

pid_tmp="$RUNTIME/worker.pid.tmp"
printf '%s\n' "$$" >"$pid_tmp"
mv -f "$pid_tmp" "$RUNTIME/worker.pid"
printf '%s ISS_THROUGHPUT_LAUNCH_OK head=%s pid=%s tables=%s workers=%s\n'   "$(date -u +%FT%TZ)" "$head" "$$" "$TABLES" "$WORKERS" >>"$LOG"

exec /tmp/skatai-v2-b0-venv/bin/python -m skatai.iss.gate_worker
