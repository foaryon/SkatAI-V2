#!/usr/bin/env bash
# Configure the RunPod template container start command to:
# /bin/bash /workspace/skatai-v2/scripts/volume_bootstrap.sh
# The network volume is mounted before this command; /opt and /pre_start.sh
# are image-layer files and cannot be relied on after pod recreation.
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
if SKATAI_RECOVERY_DEFER_BOOT=1 bash "$repo/scripts/recover_orchestrator_on_pod.sh" >>"$log" 2>&1; then
  printf 'RECOVERY_OK %s\n' "$(date -u +%FT%TZ)" >>"$log"
else
  printf 'RECOVERY_FAILED %s; pod remains accessible for diagnosis\n' "$(date -u +%FT%TZ)" >>"$log"
fi
exec /bin/bash /start.sh
