#!/usr/bin/env bash
set -euo pipefail
umask 027

JOB_ID=cardplay-diagnostic-20260925-v1
ROOT=/workspace/skatai-v2-wt-cardplay-diag
RUNTIME=/workspace/skatai-v2-runtime/isolated-science/$JOB_ID
SAMPLE=/tmp/$JOB_ID-sample.jsonl
OUTPUT=$RUNTIME/result.json
SAMPLE_SHA=c27564167395b7775bfa16b3b8e2c1be9fec587f426cd55481832078f33b8805
SELECTION_SHA=2487bf24066f533e0137025e6ddd0592bc899223349de333440cfbf8999d989a
S3_ARGS=(--s3-provider Other --s3-env-auth --s3-endpoint https://fsn1.your-objectstorage.com --s3-region fsn1)

mkdir -p "$RUNTIME"
status() {
  local state="$1" detail="${2:-}"
  python3 - "$RUNTIME/status.json" "$state" "$detail" <<'PY'
import json,os,sys,tempfile,time
path,state,detail=sys.argv[1:]
payload={"schema":"skatai.v2.isolated-cardplay-diagnostic-job.v1","job_id":"cardplay-diagnostic-20260925-v1","state":state,"detail":detail,"unix_ns":time.time_ns()}
fd,tmp=tempfile.mkstemp(prefix="status.",dir=os.path.dirname(path))
with os.fdopen(fd,"w") as f:
 json.dump(payload,f,sort_keys=True);f.write("\n");f.flush();os.fsync(f.fileno())
os.replace(tmp,path)
PY
}

finish() {
  local rc="$?"
  if [ "$rc" -ne 0 ]; then status FAILED "exit:$rc" || true; fi
  # Best-effort self-termination bounds cost. MAIN independently reconciles
  # this pod by ID and deletes it if this request has an unknown outcome.
  if [ -n "${RUNPOD_DEPLOY_API_KEY:-}" ] && [ -n "${RUNPOD_POD_ID:-}" ]; then
    curl -fsS -m 20 -X DELETE -H "Authorization: Bearer $RUNPOD_DEPLOY_API_KEY" \
      "https://api.runpod.io/v2/pods/$RUNPOD_POD_ID" >/dev/null 2>&1 || true
  fi
}
trap finish EXIT

status STARTING
test "${SKATAI_NODE_ROLE:-}" = ISOLATED_SCIENCE
test "$(git -C "$ROOT" rev-parse HEAD)" = "${SKATAI_DIAGNOSTIC_SOURCE_COMMIT:?}"
test -z "$(git -C "$ROOT" status --porcelain)"
test -n "${AWS_ACCESS_KEY_ID:-}" && test -n "${AWS_SECRET_ACCESS_KEY:-}"

timeout 900 "$ROOT/scripts/bootstrap-local-b1-pregate.sh" >"$RUNTIME/bootstrap.log" 2>&1
status BOOTSTRAPPED
rclone copyto \
  ":s3:skatai-v2/datasets/research/cardplay-train-range-sample/$SAMPLE_SHA.jsonl" \
  "$SAMPLE.tmp" "${S3_ARGS[@]}" >"$RUNTIME/stage.log" 2>&1
test "$(sha256sum "$SAMPLE.tmp" | cut -d' ' -f1)" = "$SAMPLE_SHA"
mv "$SAMPLE.tmp" "$SAMPLE"
status EVALUATING
timeout 1500 env PYTHONPATH="$ROOT/src" /tmp/skatai-v2-b0-venv/bin/python \
  "$ROOT/scripts/diagnose-b0-cardplay-agreement.py" \
  --input "$SAMPLE" --expected-input-sha256 "$SAMPLE_SHA" \
  --source-asset-id legacy-v1-canonical-corpus \
  --expected-selection-sha256 "$SELECTION_SHA" \
  --max-rows 882 --per-stratum 4 --seed 20260925 \
  --output "$OUTPUT.tmp" >"$RUNTIME/diagnostic.log" 2>&1
test "$(/tmp/skatai-v2-b0-venv/bin/python -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["selection"]["identity_sha256"])' "$OUTPUT.tmp")" = "$SELECTION_SHA"
mv "$OUTPUT.tmp" "$OUTPUT"
RESULT_SHA="$(sha256sum "$OUTPUT" | cut -d' ' -f1)"
status UPLOADING "$RESULT_SHA"
REMOTE=":s3:skatai-v2/evidence/cardplay-diagnostic/$JOB_ID/$RESULT_SHA.json"
rclone copyto "$OUTPUT" "$REMOTE" "${S3_ARGS[@]}" >"$RUNTIME/upload.log" 2>&1
rclone cat "$REMOTE" "${S3_ARGS[@]}" >"$RUNTIME/remote-readback.json.tmp"
test "$(sha256sum "$RUNTIME/remote-readback.json.tmp" | cut -d' ' -f1)" = "$RESULT_SHA"
rm -f "$RUNTIME/remote-readback.json.tmp"
status COMPLETE "$RESULT_SHA"
