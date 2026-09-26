#!/usr/bin/env bash
# Run from the persistent SentinelX entrypoint before its /start.sh exec.
# SKATAI_BOOTSTRAP_DEFER_START=1 returns to SentinelX after recovery.
# /opt and /pre_start.sh are image-layer files that disappear on pod recreation.
set -u
repo=/workspace/skatai-v2
runtime=/workspace/skatai-v2-runtime
log="$runtime/orchestrator/control/volume-bootstrap.log"
mkdir -p "$(dirname "$log")"
chmod 700 "$(dirname "$log")"
if [ -x "$runtime/toolchains/jdk-25/bin/java" ] &&
   [ -x "$runtime/toolchains/jdk-25/bin/javac" ]; then
  ln -sfn "$runtime/toolchains/jdk-25/bin/java" /usr/local/bin/java
  ln -sfn "$runtime/toolchains/jdk-25/bin/javac" /usr/local/bin/javac
fi
recovery_ok=0
if SKATAI_RECOVERY_DEFER_BOOT=1 bash "$repo/scripts/recover_orchestrator_on_pod.sh" >>"$log" 2>&1; then
  recovery_ok=1
  printf 'RECOVERY_OK %s\n' "$(date -u +%FT%TZ)" >>"$log"
else
  printf 'RECOVERY_FAILED %s; pod remains accessible for diagnosis\n' "$(date -u +%FT%TZ)" >>"$log"
fi
if [ "${SKATAI_BOOTSTRAP_DEFER_START:-0}" = "1" ]; then
  [ "$recovery_ok" = "1" ]
  exit
fi
exec /bin/bash /start.sh
