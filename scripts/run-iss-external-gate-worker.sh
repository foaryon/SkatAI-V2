#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=/workspace/skatai-v2/src
exec /tmp/skatai-v2-dev/bin/python -m skatai.iss.gate_worker "$@"
