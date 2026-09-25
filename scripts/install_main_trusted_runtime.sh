#!/usr/bin/env bash
set -euo pipefail
umask 077

REPO=/workspace/skatai-v2
BASE=/opt/skatai-main-controller
RELEASES="$BASE/releases"
CONTROL=/var/lib/skatai-main-controller

if [ "$(id -u)" -ne 0 ]; then
  echo "TRUSTED_RUNTIME_INSTALL_REQUIRES_ROOT" >&2
  exit 1
fi

cd "$REPO"
# Installation is always from the exact committed HEAD. Unrelated dirty
# scientific/runtime work is deliberately ignored rather than copied.
head="$(git rev-parse HEAD)"
git cat-file -e "$head^{commit}"

# Bootstrap the root-only state from exact committed helper content.
tmpctl="$(mktemp)"
tmpuser="$(mktemp)"
trap 'rm -f "$tmpctl" "$tmpuser"' EXIT
git show "$head:scripts/prepare_main_control_plane.sh" >"$tmpctl"
git show "$head:scripts/prepare_main_executor_user.sh" >"$tmpuser"
chmod 700 "$tmpctl" "$tmpuser"
bash "$tmpctl"
bash "$tmpuser"

install -d -m 755 -o root -g root "$BASE" "$RELEASES"
release="$RELEASES/$head"
stage="$RELEASES/.stage-$head-$$"
rm -rf "$stage"
install -d -m 755 -o root -g root "$stage" "$stage/config" "$stage/authority"

extract() {
  src="$1"
  dst="$2"
  mode="$3"
  git cat-file -e "$head:$src"
  git show "$head:$src" >"$stage/$dst"
  chown root:root "$stage/$dst"
  chmod "$mode" "$stage/$dst"
}

extract scripts/openai_platform_main_controller.py openai_platform_main_controller.py 755
extract scripts/main_tool_gateway.py main_tool_gateway.py 755
extract scripts/check_main_startup.py check_main_startup.py 755
extract scripts/openai_agent_boot.sh openai_agent_boot.sh 755
extract scripts/openai_platform_guardian.sh openai_platform_guardian.sh 755
extract scripts/prepare_main_executor_user.sh prepare_main_executor_user.sh 755
extract scripts/prepare_runtime_secrets.py prepare_runtime_secrets.py 755
extract scripts/submit_main_user_input.py submit_main_user_input.py 755
extract scripts/validate_main_agent_readiness.py validate_main_agent_readiness.py 755
extract scripts/validate_main_controller_logic.py validate_main_controller_logic.py 755
extract scripts/approve_main_reenable.py approve_main_reenable.py 755
extract scripts/r9_trusted_boot.sh r9_trusted_boot.sh 755
extract scripts/launch_frozen_r9_supervisor.py launch_frozen_r9_supervisor.py 755
extract scripts/supervise_frozen_r9.py supervise_frozen_r9.py 755

extract configs/control/MAIN_AGENT_INSTRUCTIONS.md config/MAIN_AGENT_INSTRUCTIONS.md 644
extract configs/control/MAIN_CONTINUE_EXECUTION_POLICY.txt config/MAIN_CONTINUE_EXECUTION_POLICY.txt 644
extract configs/control/MAIN_EXECUTION_GOVERNOR.json config/MAIN_EXECUTION_GOVERNOR.json 644
extract configs/control/MAIN_GOAL_POLICY.json config/MAIN_GOAL_POLICY.json 644
extract provenance/MAIN_EXECUTION_LOCK.json config/INITIAL_EXECUTION_LOCK.json 644

extract SKATAI_V2_FOUNDING_SPECIFICATION.md authority/SKATAI_V2_FOUNDING_SPECIFICATION.md 644
extract SKATAI_V2_WORK_PROMPT.md authority/SKATAI_V2_WORK_PROMPT.md 644
extract SKATAI_V2_MASTER_CONTINUE_MERGED.md authority/SKATAI_V2_MASTER_CONTINUE_MERGED.md 644

printf '%s\n' "$head" >"$stage/SOURCE_COMMIT"
chmod 644 "$stage/SOURCE_COMMIT"
chown root:root "$stage/SOURCE_COMMIT"

(
  cd "$stage"
  find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum >MANIFEST.sha256
  sha256sum -c MANIFEST.sha256 >/dev/null
)
chmod 644 "$stage/MANIFEST.sha256"
chown -R root:root "$stage"
find "$stage" -type d -exec chmod 755 {} +

rm -rf "$release"
mv "$stage" "$release"
ln -sfn "$release" "$BASE/current.new"
mv -Tf "$BASE/current.new" "$BASE/current"

# Root-owned active authority. MAIN itself has no writable/self-hosted
# environment; all material effects cross the trusted function gateway.
install -m 0600 -o root -g root "$release/config/INITIAL_EXECUTION_LOCK.json" "$CONTROL/EXECUTION_LOCK.json"

cat >/pre_start.sh <<'EOF'
#!/usr/bin/env bash
set +e
TRUST=/opt/skatai-main-controller/current
CONTROL=/var/lib/skatai-main-controller
[ -d "$TRUST" ] || exit 0
(
  cd "$TRUST" || exit 1
  sha256sum -c MANIFEST.sha256 >"$CONTROL/trusted-manifest-check.log" 2>&1
) || exit 0
[ -x "$TRUST/r9_trusted_boot.sh" ] && "$TRUST/r9_trusted_boot.sh"
[ -x "$TRUST/openai_agent_boot.sh" ] && "$TRUST/openai_agent_boot.sh"
exit 0
EOF
chown root:root /pre_start.sh
chmod 755 /pre_start.sh

printf 'TRUSTED_RUNTIME_INSTALLED commit=%s release=%s\n' "$head" "$release"
