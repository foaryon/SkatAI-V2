#!/usr/bin/env bash
set -euo pipefail

V2_ROOT=/workspace/skatai-v2
CACHE_ROOT=/tmp
B0_ROOT=$CACHE_ROOT/skatai-v2-b0
B0_VENV=$CACHE_ROOT/skatai-v2-b0-venv
B1_ROOT=$CACHE_ROOT/skatai-v2-b1-linearish-full-v1
B1_MODEL=$B1_ROOT/model.pt
SOURCE_TAR=$CACHE_ROOT/skatai-v2-b0-source.tar
EXPECTED_SOURCE_SHA=40eff4a1f03aa0a0c0665a11b733505130a79e8ac646195c8dc51c61b6fb3f2d
EXPECTED_COMMIT=1fe5cabbd5f9c3e77ab51714b0ac702e5a71e53b
EXPECTED_B1_SHA=fbe8b1f97ee6f6e169fd9a516126f2ae75149562e2abc41e89f2da14d3761ab4
S3_ENDPOINT=https://fsn1.your-objectstorage.com
S3_REGION=fsn1

s3_copyto() {
  rclone copyto "$1" "$2" \
    --s3-provider Other --s3-env-auth \
    --s3-endpoint "$S3_ENDPOINT" --s3-region "$S3_REGION"
}

source_ok=false
if [ -d "$B0_ROOT/.git" ]; then
  if [ "$(git -C "$B0_ROOT" rev-parse HEAD 2>/dev/null || true)" = "$EXPECTED_COMMIT" ]; then
    source_ok=true
  fi
elif [ -f "$B0_ROOT/.skatai-upstream-commit" ] && [ -f "$B0_ROOT/.skatai-source-archive-sha256" ]; then
  if [ "$(cat "$B0_ROOT/.skatai-upstream-commit")" = "$EXPECTED_COMMIT" ] && \
     [ "$(cat "$B0_ROOT/.skatai-source-archive-sha256")" = "$EXPECTED_SOURCE_SHA" ]; then
    source_ok=true
  fi
fi

if [ "$source_ok" != true ]; then
  rm -rf "$B0_ROOT.tmp"
  rm -f "$SOURCE_TAR.tmp"
  s3_copyto :s3:skatai-v2/models/V2-B0/skatzero-source.tar "$SOURCE_TAR.tmp"
  test "$(sha256sum "$SOURCE_TAR.tmp" | cut -d' ' -f1)" = "$EXPECTED_SOURCE_SHA"
  mv "$SOURCE_TAR.tmp" "$SOURCE_TAR"
  mkdir -p "$B0_ROOT.tmp"
  tar -xf "$SOURCE_TAR" -C "$B0_ROOT.tmp"
  printf '%s\n' "$EXPECTED_COMMIT" >"$B0_ROOT.tmp/.skatai-upstream-commit"
  printf '%s\n' "$EXPECTED_SOURCE_SHA" >"$B0_ROOT.tmp/.skatai-source-archive-sha256"
  rm -rf "$B0_ROOT"
  mv "$B0_ROOT.tmp" "$B0_ROOT"
fi

mkdir -p "$B0_ROOT/models/latest"
python3 - <<'PY'
import hashlib, json
from pathlib import Path
manifest=json.load(open("/workspace/skatai-v2/provenance/B0_SKATZERO_BASELINE.json"))
root=Path("/tmp/skatai-v2-b0/models/latest")
missing=[]
for name,expected in sorted(manifest["pretrained_models"].items()):
    p=root/name
    if not p.exists():
        missing.append(name)
        continue
    h=hashlib.sha256(p.read_bytes()).hexdigest()
    if h != expected:
        p.unlink()
        missing.append(name)
Path("/tmp/skatai-v2-b0-missing-models.txt").write_text("\n".join(missing)+("\n" if missing else ""))
print("missing_models",len(missing))
PY

while IFS= read -r name; do
  [ -n "$name" ] || continue
  s3_copyto ":s3:skatai-v2/models/V2-B0/pretrained/$name" "$B0_ROOT/models/latest/$name.tmp"
  mv "$B0_ROOT/models/latest/$name.tmp" "$B0_ROOT/models/latest/$name"
done </tmp/skatai-v2-b0-missing-models.txt

python3 - <<'PY'
import hashlib, json
from pathlib import Path
manifest=json.load(open("/workspace/skatai-v2/provenance/B0_SKATZERO_BASELINE.json"))
root=Path("/tmp/skatai-v2-b0/models/latest")
for name,expected in sorted(manifest["pretrained_models"].items()):
    p=root/name
    h=hashlib.sha256(p.read_bytes()).hexdigest()
    if h != expected:
        raise SystemExit(f"B0_MODEL_HASH_MISMATCH:{name}:{h}!={expected}")
print("B0_MODELS_VERIFIED")
PY

mkdir -p "$B1_ROOT"
if [ ! -f "$B1_MODEL" ] || [ "$(sha256sum "$B1_MODEL" | cut -d' ' -f1)" != "$EXPECTED_B1_SHA" ]; then
  rm -f "$B1_MODEL.tmp"
  s3_copyto :s3:skatai-v2/models/V2-B1-candidates/bidding-linearish-full-v1/model.pt "$B1_MODEL.tmp"
  test "$(sha256sum "$B1_MODEL.tmp" | cut -d' ' -f1)" = "$EXPECTED_B1_SHA"
  mv "$B1_MODEL.tmp" "$B1_MODEL"
fi

if [ ! -x "$B0_VENV/bin/python" ]; then
  rm -rf "$B0_VENV"
  uv venv --python /usr/bin/python3.11 "$B0_VENV"
  uv pip install --python "$B0_VENV/bin/python" \
    --index-url https://download.pytorch.org/whl/cpu torch==2.1.2
  uv pip install --python "$B0_VENV/bin/python" numpy==1.26.4
fi

"$B0_VENV/bin/python" - <<'PY'
import sys, torch, numpy
if sys.version_info[:2] != (3,11):
    raise SystemExit(f"BAD_PYTHON:{sys.version}")
if torch.__version__ != "2.1.2+cpu":
    raise SystemExit(f"BAD_TORCH:{torch.__version__}")
if numpy.__version__ != "1.26.4":
    raise SystemExit(f"BAD_NUMPY:{numpy.__version__}")
print("B0_RUNTIME_VERIFIED",sys.version.split()[0],torch.__version__,numpy.__version__)
PY

# Verify frozen implementation surfaces even when source was restored from tar.
python3 - <<'PY'
import hashlib,json
from pathlib import Path
m=json.load(open("/workspace/skatai-v2/provenance/B0_SKATZERO_BASELINE.json"))
root=Path("/tmp/skatai-v2-b0")
checks={
  "inference_api_py": root/"api.py",
  "train_py": root/"train.py",
  "evaluate_py": root/"evaluate.py",
  "dmc_model_py": root/"skatzero/dmc/model.py",
  "dmc_neural_net_py": root/"skatzero/dmc/neural_net.py",
  "dmc_trainer_py": root/"skatzero/dmc/trainer.py",
}
for key,p in checks.items():
    got=hashlib.sha256(p.read_bytes()).hexdigest()
    expected=m["implementation_hashes"][key]
    if got != expected:
        raise SystemExit(f"B0_SOURCE_HASH_MISMATCH:{key}:{got}!={expected}")
print("B0_SOURCE_SURFACES_VERIFIED")
PY

printf 'B0_ROOT=%s\nB0_VENV=%s\nB1_MODEL=%s\n' "$B0_ROOT" "$B0_VENV" "$B1_MODEL"
