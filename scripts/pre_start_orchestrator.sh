#!/usr/bin/env bash
set +e
if [ "${SKATAI_NODE_ROLE:-}" = "ISOLATED_SCIENCE" ]; then exit 0; fi
legacy=/opt/skatai-main-controller/current
if [ -x "$legacy/r9_trusted_boot.sh" ]; then
  (cd "$legacy" && sha256sum -c MANIFEST.sha256 >/var/lib/skatai-main-controller/trusted-manifest-check.log 2>&1) && "$legacy/r9_trusted_boot.sh"
fi
orchestrator=/opt/skatai-orchestrator/current
if [ -x "$orchestrator/orchestrator_boot.sh" ]; then
  (cd "$orchestrator" && sha256sum -c MANIFEST.sha256 >/var/lib/skatai-orchestrator/trusted-manifest-check.log 2>&1) && "$orchestrator/orchestrator_boot.sh"
fi
exit 0
