#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'TXT'
usage:
  run-b1-local-stage.sh \
    --stage SCREEN|LOCAL_CONFIRMATION \
    --deal-set PATH \
    --deal-count N \
    --output-dir PATH \
    [--selection-seed N] [--master-seed N]
TXT
  exit 2
}

STAGE=
DEAL_SET=
DEAL_COUNT=
OUTPUT_DIR=
SELECTION_SEED=20260923
MASTER_SEED=20260923

while [ "$#" -gt 0 ]; do
  case "$1" in
    --stage) STAGE=$2; shift 2 ;;
    --deal-set) DEAL_SET=$2; shift 2 ;;
    --deal-count) DEAL_COUNT=$2; shift 2 ;;
    --output-dir) OUTPUT_DIR=$2; shift 2 ;;
    --selection-seed) SELECTION_SEED=$2; shift 2 ;;
    --master-seed) MASTER_SEED=$2; shift 2 ;;
    *) usage ;;
  esac
done

[ -n "$STAGE" ] && [ -n "$DEAL_SET" ] && [ -n "$DEAL_COUNT" ] && [ -n "$OUTPUT_DIR" ] || usage
case "$STAGE" in
  SCREEN)
    [ "$DEAL_COUNT" = "30" ] || { echo "SCREEN requires 30 deals" >&2; exit 2; }
    REMOTE_STAGE=screen
    ;;
  LOCAL_CONFIRMATION)
    [ "$DEAL_COUNT" = "100" ] || { echo "LOCAL_CONFIRMATION requires 100 deals" >&2; exit 2; }
    REMOTE_STAGE=local-confirmation
    ;;
  *) usage ;;
esac

V2_ROOT=/workspace/skatai-v2
SKATZERO_ROOT=/tmp/skatai-v2-b0
B0_MODEL_ROOT=/tmp/skatai-v2-b0/models/latest
B0_PYTHON=/tmp/skatai-v2-b0-venv/bin/python
B1_ROOT=/tmp/skatai-v2-b1-linearish-full-v1
B1_MODEL=$B1_ROOT/model.pt
EXPECTED_SKATZERO_COMMIT=1fe5cabbd5f9c3e77ab51714b0ac702e5a71e53b
EXPECTED_B1_SHA=fbe8b1f97ee6f6e169fd9a516126f2ae75149562e2abc41e89f2da14d3761ab4

mkdir -p "$OUTPUT_DIR"
RESULT=$OUTPUT_DIR/result.json
CHECKPOINT=$OUTPUT_DIR/checkpoint.json
DECISION=$OUTPUT_DIR/decision.json
RUN_IDENTITY=$OUTPUT_DIR/run-identity.json

test -x "$B0_PYTHON"
test -f "$B1_MODEL"
test -f "$DEAL_SET"
test "$(git -C "$SKATZERO_ROOT" rev-parse HEAD)" = "$EXPECTED_SKATZERO_COMMIT"
test "$(sha256sum "$B1_MODEL" | cut -d' ' -f1)" = "$EXPECTED_B1_SHA"

"$B0_PYTHON" - <<'PY'
import hashlib,json
from pathlib import Path
manifest=json.load(open("/workspace/skatai-v2/provenance/B0_SKATZERO_BASELINE.json"))
root=Path("/tmp/skatai-v2-b0/models/latest")
for name,expected in sorted(manifest["pretrained_models"].items()):
    p=root/name
    h=hashlib.sha256(p.read_bytes()).hexdigest()
    if h != expected:
        raise SystemExit(f"B0_MODEL_HASH_MISMATCH:{name}:{h}!={expected}")
print("B0_MODEL_HASHES_OK")
PY

DEAL_SET_SHA=$(sha256sum "$DEAL_SET" | cut -d' ' -f1)
B1_SHA=$(sha256sum "$B1_MODEL" | cut -d' ' -f1)
V2_COMMIT=$(git -C "$V2_ROOT" rev-parse HEAD)

cat >"$RUN_IDENTITY.tmp" <<EOF2
{
  "schema": "skatai.v2.local-bidding-stage-run.v1",
  "stage": "$STAGE",
  "candidate": "V2-B1-bidding-linearish-full-v1",
  "control": "V2-B0",
  "v2_commit": "$V2_COMMIT",
  "deal_set": {
    "path": "$DEAL_SET",
    "sha256": "$DEAL_SET_SHA",
    "count": $DEAL_COUNT
  },
  "b0": {
    "skatzero_commit": "$EXPECTED_SKATZERO_COMMIT",
    "accuracy": 231,
    "bid_threshold": -5.0
  },
  "b1": {
    "model_sha256": "$B1_SHA",
    "threshold": 0.5
  },
  "selection_seed": $SELECTION_SEED,
  "master_seed": $MASTER_SEED,
  "checkpoint": "$CHECKPOINT",
  "result": "$RESULT",
  "decision": "$DECISION"
}
EOF2
mv "$RUN_IDENTITY.tmp" "$RUN_IDENTITY"

PYTHONPATH=$V2_ROOT/src "$B0_PYTHON" \
  -m skatai.evaluation.bidding_gameplay_gate \
  --deal-set "$DEAL_SET" \
  --skatzero-root "$SKATZERO_ROOT" \
  --model-root "$B0_MODEL_ROOT" \
  --b1-model "$B1_MODEL" \
  --output "$RESULT" \
  --checkpoint "$CHECKPOINT" \
  --deal-count "$DEAL_COUNT" \
  --selection-seed "$SELECTION_SEED" \
  --master-seed "$MASTER_SEED" \
  --b1-threshold 0.5 \
  --b0-accuracy 231 \
  --b0-bid-threshold -5.0

PYTHONPATH=$V2_ROOT/src "$B0_PYTHON" \
  -m skatai.evaluation.bidding_gate_decision \
  "$RESULT" --stage "$STAGE" --output "$DECISION"

RESULT_SHA=$(sha256sum "$RESULT" | cut -d' ' -f1)
CHECKPOINT_SHA=$(sha256sum "$CHECKPOINT" | cut -d' ' -f1)
DECISION_SHA=$(sha256sum "$DECISION" | cut -d' ' -f1)
RUN_SHA=$(sha256sum "$RUN_IDENTITY" | cut -d' ' -f1)

REMOTE=":s3:skatai-v2/evidence/V2-B1-bidding-linearish-full-v1/local-pregate/$REMOTE_STAGE"
for item in \
  "$RESULT:result.json" \
  "$CHECKPOINT:checkpoint.json" \
  "$DECISION:decision.json" \
  "$RUN_IDENTITY:run-identity.json"
do
  src=$(printf '%s' "$item" | cut -d: -f1)
  name=$(printf '%s' "$item" | cut -d: -f2-)
  rclone copyto "$src" "$REMOTE/$name" \
    --s3-provider Other --s3-env-auth \
    --s3-endpoint https://fsn1.your-objectstorage.com --s3-region fsn1
done

verify_remote() {
  local name=$1
  local expected=$2
  local got
  got=$(rclone cat "$REMOTE/$name" \
    --s3-provider Other --s3-env-auth \
    --s3-endpoint https://fsn1.your-objectstorage.com --s3-region fsn1 \
    | sha256sum | cut -d' ' -f1)
  test "$got" = "$expected"
}

verify_remote result.json "$RESULT_SHA"
verify_remote checkpoint.json "$CHECKPOINT_SHA"
verify_remote decision.json "$DECISION_SHA"
verify_remote run-identity.json "$RUN_SHA"

printf 'stage=%s\nresult_sha256=%s\ncheckpoint_sha256=%s\ndecision_sha256=%s\nrun_identity_sha256=%s\n' \
  "$STAGE" "$RESULT_SHA" "$CHECKPOINT_SHA" "$DECISION_SHA" "$RUN_SHA"
