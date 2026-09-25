#!/usr/bin/env bash
set -euo pipefail
umask 027

JOB_ID=endgame-pimc-screen-20260925-v1
ROOT=/workspace/skatai-v2-wt-pimc-screen
RUNTIME=/workspace/skatai-v2-runtime/isolated-science/$JOB_ID
TASK=$ROOT/provenance/ENDGAME_PIMC_PAIRED_SCREEN_TASK_20260925.json
OUTPUT=$RUNTIME/result.json
S3_ARGS=(--s3-provider Other --s3-env-auth --s3-endpoint https://fsn1.your-objectstorage.com --s3-region fsn1)
mkdir -p "$RUNTIME"

status() {
  local state="$1" detail="${2:-}"
  python3 - "$RUNTIME/status.json" "$state" "$detail" <<'PY'
import json,os,sys,tempfile,time
path,state,detail=sys.argv[1:]
payload={"schema":"skatai.v2.isolated-pimc-screen-job.v1","state":state,"detail":detail,"unix_ns":time.time_ns()}
fd,tmp=tempfile.mkstemp(prefix="status.",dir=os.path.dirname(path))
with os.fdopen(fd,"w") as f:
 json.dump(payload,f,sort_keys=True);f.write("\n");f.flush();os.fsync(f.fileno())
os.replace(tmp,path)
with open(os.path.join(os.path.dirname(path),"events.jsonl"),"a") as f:
 f.write(json.dumps(payload,sort_keys=True)+"\n");f.flush();os.fsync(f.fileno())
PY
}
fail() { status FAILED "$1"; exit 1; }
finish() {
  local rc="$?"
  if [ "$rc" -ne 0 ]; then status FAILED "exit:$rc" || true; fi
  if [ -n "${RUNPOD_DEPLOY_API_KEY:-}" ] && [ -n "${RUNPOD_POD_ID:-}" ]; then
    curl -fsS -m 20 -X DELETE -H "Authorization: Bearer $RUNPOD_DEPLOY_API_KEY" \
      "https://api.runpod.io/v2/pods/$RUNPOD_POD_ID" >/dev/null 2>&1 || true
  fi
}
trap finish EXIT

status STARTING
[ "${SKATAI_NODE_ROLE:-}" = ISOLATED_SCIENCE ] || fail ROLE_NOT_ISOLATED_SCIENCE
[ -n "${SKATAI_PIMC_SOURCE_COMMIT:-}" ] || fail SOURCE_COMMIT_MISSING
[ -n "${SKATAI_PIMC_TASK_SHA256:-}" ] || fail TASK_SHA256_MISSING
[ -d "$ROOT" ] || fail PINNED_WORKTREE_MISSING
actual="$(git -c "safe.directory=$ROOT" -C "$ROOT" rev-parse HEAD 2>"$RUNTIME/git-preflight.log" || true)"
[ "$actual" = "$SKATAI_PIMC_SOURCE_COMMIT" ] || fail "SOURCE_COMMIT_MISMATCH:$actual"
[ -z "$(git -c "safe.directory=$ROOT" -C "$ROOT" status --porcelain 2>>"$RUNTIME/git-preflight.log" || true)" ] || fail PINNED_WORKTREE_DIRTY
[ "$(sha256sum "$TASK" | cut -d' ' -f1)" = "$SKATAI_PIMC_TASK_SHA256" ] || fail TASK_HASH_MISMATCH
[ -n "${AWS_ACCESS_KEY_ID:-}" ] || fail S3_ACCESS_KEY_MISSING
[ -n "${AWS_SECRET_ACCESS_KEY:-}" ] || fail S3_SECRET_KEY_MISSING
status PREFLIGHT_OK

timeout 900 "$ROOT/scripts/bootstrap-local-b1-pregate.sh" >"$RUNTIME/bootstrap.log" 2>&1
status BOOTSTRAPPED
timeout 1500 env PYTHONPATH="$ROOT/src" /tmp/skatai-v2-b0-venv/bin/python \
  "$ROOT/scripts/run_endgame_pimc_paired_screen.py" --task "$TASK" \
  --output "$OUTPUT.tmp" >"$RUNTIME/screen.log" 2>&1
mv "$OUTPUT.tmp" "$OUTPUT"
result_sha="$(sha256sum "$OUTPUT" | cut -d' ' -f1)"
status UPLOADING "$result_sha"
remote=":s3:skatai-v2/evidence/endgame-pimc/paired-screen/$JOB_ID/$result_sha.json"
rclone copyto "$OUTPUT" "$remote" "${S3_ARGS[@]}" >"$RUNTIME/upload.log" 2>&1
rclone cat "$remote" "${S3_ARGS[@]}" >"$RUNTIME/remote-readback.json.tmp"
[ "$(sha256sum "$RUNTIME/remote-readback.json.tmp" | cut -d' ' -f1)" = "$result_sha" ] || fail REMOTE_HASH_MISMATCH
rm -f "$RUNTIME/remote-readback.json.tmp"
status COMPLETE "$result_sha"
