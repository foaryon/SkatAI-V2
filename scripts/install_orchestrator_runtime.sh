#!/usr/bin/env bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo 'ROOT_REQUIRED' >&2; exit 1; }
repo=/workspace/skatai-v2
base=/opt/skatai-orchestrator
control=/var/lib/skatai-orchestrator
cd "$repo"
python3 -m py_compile src/skatai/orchestration/control_plane.py scripts/orchestrator_superbrain_controller.py scripts/prepare_shared_runtime_secrets.py
install -d -m 700 "$base/releases" "$control"
release="$(git -c safe.directory=/workspace/skatai-v2 rev-parse --short=12 HEAD)-$(date -u +%Y%m%dT%H%M%SZ)"
stage="$base/releases/.$release.tmp"
install -d -m 700 "$stage/config" "$stage/authority"
install -m 600 src/skatai/orchestration/control_plane.py "$stage/control_plane.py"
install -m 600 scripts/prepare_shared_runtime_secrets.py "$stage/prepare_shared_runtime_secrets.py"
install -m 700 scripts/orchestrator_boot.sh "$stage/orchestrator_boot.sh"
install -m 700 scripts/orchestrator_supervisor.sh "$stage/orchestrator_supervisor.sh"
install -m 600 configs/orchestration/*json configs/orchestration/*md configs/orchestration/*yaml "$stage/config/"
install -m 600 SKATAI_V2_FOUNDING_SPECIFICATION.md SKATAI_V2_WORK_PROMPT.md "$stage/authority/"
(cd "$stage" && find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum >MANIFEST.sha256 && sha256sum -c MANIFEST.sha256)
chmod -R go-rwx "$stage"
mv "$stage" "$base/releases/$release"
ln -sfn "releases/$release" "$base/.current.new"
mv -Tf "$base/.current.new" "$base/current"
install -d -m 700 /workspace/skatai-v2-runtime/orchestrator/state
printf 'INSTALLED %s\n' "$base/releases/$release"
