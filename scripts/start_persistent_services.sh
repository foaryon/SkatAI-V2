#!/usr/bin/env bash
set +e

# Isolated science pods share the source volume but have no authority to start
# MAIN or the frozen external ISS supervisor.
if [ "${SKATAI_NODE_ROLE:-}" = "ISOLATED_SCIENCE" ]; then
  exit 0
fi

# Scientific ISS work is intentionally independent from MAIN.
if [ -x /workspace/skatai-v2/scripts/start_frozen_r9_supervisor.sh ]; then
  /workspace/skatai-v2/scripts/start_frozen_r9_supervisor.sh
fi

# MAIN owns autonomous project execution, not the lifetime of ISS workers.
if [ -x /workspace/openai-agent/boot.sh ]; then
  /workspace/openai-agent/boot.sh
fi

exit 0
