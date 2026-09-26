#!/usr/bin/env bash
set +e
if [ "${SKATAI_NODE_ROLE:-}" = "ISOLATED_SCIENCE" ]; then
  exit 0
fi
if [ -x /opt/skatai-main-controller/current/r9_trusted_boot.sh ]; then
  (cd /opt/skatai-main-controller/current && sha256sum -c MANIFEST.sha256 >/var/lib/skatai-main-controller/trusted-manifest-check.log 2>&1) && /opt/skatai-main-controller/current/r9_trusted_boot.sh
fi
[ -x /opt/skatai-orchestrator/current/orchestrator_boot.sh ] && /opt/skatai-orchestrator/current/orchestrator_boot.sh
exit 0
