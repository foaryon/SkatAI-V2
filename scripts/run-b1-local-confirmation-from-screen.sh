#!/usr/bin/env bash
set -euo pipefail

SCREEN_DIR=/workspace/skatai-v2-runtime/local-pregate/screen-30-v1
CONFIRM_DIR=/workspace/skatai-v2-runtime/local-pregate/local-confirmation-100-v1
SCREEN_SET=/tmp/skatai-v2-pregate-dealsets/screen-30.json
CONFIRM_SET=/tmp/skatai-v2-pregate-dealsets/local-confirmation-100.json

if [ "$#" -ge 1 ]; then SCREEN_DIR=$1; fi
if [ "$#" -ge 2 ]; then CONFIRM_DIR=$2; fi
if [ "$#" -ge 3 ]; then SCREEN_SET=$3; fi
if [ "$#" -ge 4 ]; then CONFIRM_SET=$4; fi

V2_ROOT=/workspace/skatai-v2
B0_ROOT=/tmp/skatai-v2-b0
B0_MODELS=/tmp/skatai-v2-b0/models/latest
B0_PYTHON=/tmp/skatai-v2-b0-venv/bin/python
B1_MODEL=/tmp/skatai-v2-b1-linearish-full-v1/model.pt

"$V2_ROOT/scripts/bootstrap-local-b1-pregate.sh"

test -f "$SCREEN_DIR/result.json"
test -f "$SCREEN_SET"
test -f "$CONFIRM_SET"
mkdir -p "$CONFIRM_DIR"

SCREEN_CLUSTERED=$SCREEN_DIR/decision-clustered.json
PYTHONPATH=$V2_ROOT/src "$B0_PYTHON" \
  -m skatai.evaluation.bidding_gate_clustered_decision \
  "$SCREEN_DIR/result.json" \
  --stage SCREEN \
  --output "$SCREEN_CLUSTERED"

NEXT_ACTION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["next_action"])' "$SCREEN_CLUSTERED")
if [ "$NEXT_ACTION" != "CONTINUE_LOCAL_CONFIRMATION" ]; then
  echo "screen clustered decision does not authorize confirmation: $NEXT_ACTION" >&2
  exit 3
fi

rclone copyto "$SCREEN_CLUSTERED" \
  :s3:skatai-v2/evidence/V2-B1-bidding-linearish-full-v1/local-pregate/screen/decision-clustered.json \
  --s3-provider Other --s3-env-auth \
  --s3-endpoint https://fsn1.your-objectstorage.com --s3-region fsn1

PYTHONPATH=$V2_ROOT/src "$B0_PYTHON" \
  -m skatai.evaluation.promote_pregate_checkpoint \
  --screen-result "$SCREEN_DIR/result.json" \
  --screen-deal-set "$SCREEN_SET" \
  --confirmation-deal-set "$CONFIRM_SET" \
  --skatzero-root "$B0_ROOT" \
  --model-root "$B0_MODELS" \
  --b1-model "$B1_MODEL" \
  --output-checkpoint "$CONFIRM_DIR/checkpoint.json"

"$V2_ROOT/scripts/run-b1-local-stage.sh" \
  --stage LOCAL_CONFIRMATION \
  --deal-set "$CONFIRM_SET" \
  --deal-count 100 \
  --output-dir "$CONFIRM_DIR"

CONFIRM_CLUSTERED=$CONFIRM_DIR/decision-clustered.json
PYTHONPATH=$V2_ROOT/src "$B0_PYTHON" \
  -m skatai.evaluation.bidding_gate_clustered_decision \
  "$CONFIRM_DIR/result.json" \
  --stage LOCAL_CONFIRMATION \
  --output "$CONFIRM_CLUSTERED"

CLUSTER_SHA=$(sha256sum "$CONFIRM_CLUSTERED" | cut -d' ' -f1)
REMOTE=:s3:skatai-v2/evidence/V2-B1-bidding-linearish-full-v1/local-pregate/local-confirmation/decision-clustered.json
rclone copyto "$CONFIRM_CLUSTERED" "$REMOTE" \
  --s3-provider Other --s3-env-auth \
  --s3-endpoint https://fsn1.your-objectstorage.com --s3-region fsn1
REMOTE_SHA=$(rclone cat "$REMOTE" \
  --s3-provider Other --s3-env-auth \
  --s3-endpoint https://fsn1.your-objectstorage.com --s3-region fsn1 \
  | sha256sum | cut -d' ' -f1)
test "$CLUSTER_SHA" = "$REMOTE_SHA"

python3 - "$CONFIRM_CLUSTERED" <<'PY'
import json,sys
x=json.load(open(sys.argv[1]))
print(json.dumps({
    "status":x["status"],
    "next_action":x["next_action"],
    "primary_stats":x["primary_stats"],
    "accept_authorized":x["accept_authorized"],
},indent=2,sort_keys=True))
PY
