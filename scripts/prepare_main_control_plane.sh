#!/usr/bin/env bash
set -euo pipefail
umask 077

CONTROL_ROOT=/var/lib/skatai-main-controller
LEGACY="$CONTROL_ROOT/legacy"
OLD_CTRL=/workspace/openai-agent/platform-controller
OLD_AGENT=/workspace/openai-agent
OLD_INBOX=/workspace/openai-agent/main-controller/inbox

if [ "$(id -u)" -ne 0 ]; then
  echo "MAIN_CONTROL_PLANE_REQUIRES_ROOT" >&2
  exit 1
fi

install -d -m 700 -o root -g root "$CONTROL_ROOT" "$CONTROL_ROOT/inbox" "$CONTROL_ROOT/inbox/processed" "$LEGACY"

# Archive legacy controller evidence, but never trust it as active control state.
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$LEGACY/$stamp"
install -d -m 700 -o root -g root "$archive"

for f in session.json controller.log exec-server.log guardian.log boot.log secret-prep.log; do
  if [ -f "$OLD_CTRL/$f" ]; then
    cp -p "$OLD_CTRL/$f" "$archive/$f"
    chmod 600 "$archive/$f"
  fi
done

if [ -d "$OLD_INBOX/processed" ]; then
  mkdir -p "$archive/processed-inbox"
  cp -p "$OLD_INBOX/processed/"*.msg "$archive/processed-inbox/" 2>/dev/null || true
  chmod -R go-rwx "$archive/processed-inbox" 2>/dev/null || true
fi

# Legacy key-wrap material must never remain on the shared 0777 workspace.
for f in openai_agents_keywrap.pem openai_agents_keywrap.pub.pem public_jwk.json; do
  if [ -f "$OLD_CTRL/$f" ]; then
    cp -p "$OLD_CTRL/$f" "$archive/$f"
    chmod 600 "$archive/$f"
    rm -f "$OLD_CTRL/$f"
  fi
done

# Legacy control files are no longer authoritative.
rm -f "$OLD_CTRL/session.json" "$OLD_CTRL/controller.pid" "$OLD_CTRL/exec-server.pid" "$OLD_CTRL/pause-submissions"
rm -f "$OLD_AGENT/AGENT_REENABLE_APPROVED"
# The function-gateway design never uses a self-hosted executor API key.
rm -f /run/skatai-v2-secrets/openai_executor_api_key

# Fail closed after every explicit installation/migration. Reenable is a
# separate operator decision bound to exact trusted policy hashes.
cat >"$CONTROL_ROOT/AGENT_HARD_DISABLED" <<'EOF'
HARD_DISABLED=1
reason=Trusted control-plane installation/migration; explicit verified reenable required.
restart_policy=DO_NOT_START until readiness validation passes and a hash-bound approval is created.
EOF
chmod 600 "$CONTROL_ROOT/AGENT_HARD_DISABLED"
rm -f "$CONTROL_ROOT/AGENT_REENABLE_APPROVED" "$CONTROL_ROOT/ACTIVE_WORK_PERMIT.json"
rm -f "$CONTROL_ROOT/EXECUTION_LOCK.json" "$CONTROL_ROOT/executor-write-scope.json"

# Keep the old marker only as defense-in-depth; it is not authoritative.
cat >"$OLD_AGENT/AGENT_HARD_DISABLED" <<'EOF'
HARD_DISABLED=1
reason=Legacy compatibility marker only; authoritative marker is root-only.
EOF

find "$CONTROL_ROOT" -maxdepth 2 -type d -exec chmod 700 {} +
find "$CONTROL_ROOT" -maxdepth 1 -type f -exec chmod 600 {} +

echo "MAIN_CONTROL_PLANE_PREPARED root=$CONTROL_ROOT archive=$archive"
