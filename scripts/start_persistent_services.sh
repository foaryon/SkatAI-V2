#!/usr/bin/env bash
set +e

# Compatibility bridge only. Root boot must execute exclusively the root-owned
# trusted snapshot, never mutable /workspace implementation code.
if [ "${SKATAI_NODE_ROLE:-}" = "ISOLATED_SCIENCE" ]; then
  exit 0
fi

TRUST=/opt/skatai-main-controller/current
[ -d "$TRUST" ] || exit 0

(
  cd "$TRUST" || exit 1
  sha256sum -c MANIFEST.sha256 >/var/lib/skatai-main-controller/trusted-manifest-check.log 2>&1
) || exit 0

[ -x "$TRUST/r9_trusted_boot.sh" ] && "$TRUST/r9_trusted_boot.sh"
[ -x "$TRUST/openai_agent_boot.sh" ] && "$TRUST/openai_agent_boot.sh"
exit 0
