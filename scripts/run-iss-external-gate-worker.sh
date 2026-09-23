#!/usr/bin/env bash
set -euo pipefail

ROOT=/workspace/skatai-v2
"$ROOT/scripts/bootstrap-local-b1-pregate.sh" >/tmp/skatai-v2-iss-asset-bootstrap.log

export PYTHONPATH="$ROOT/src"
exec /tmp/skatai-v2-b0-venv/bin/python -m skatai.iss.gate_worker "$@"
