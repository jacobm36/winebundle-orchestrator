#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

chmod +x "$ROOT/bin/winebundle-vm" "$ROOT/tests/fixtures/fake-multipass"
: >"$TEMP/installer.exe"

run_case() {
  local name=$1 expected_status=$2
  local state="$TEMP/$name-state" output="$TEMP/$name-output"
  mkdir -p "$state"

  local actual_status
  if FAKE_MULTIPASS_STATE="$state" \
    FAKE_MULTIPASS_JOB_STATUS="$expected_status" \
    MULTIPASS_COMMAND="$ROOT/tests/fixtures/fake-multipass" \
    "$ROOT/bin/winebundle-vm" run \
      --installer "$TEMP/installer.exe" \
      --install-arg /S \
      --entrypoint 'C:\Program Files\Test App\app.exe' \
      --smoke-arg --version \
      --output "$output" \
      --skip-provision >/dev/null 2>&1; then
    actual_status=0
  else
    actual_status=$?
  fi

  if (( expected_status == 0 )); then
    (( actual_status == 0 )) || fail "$name unexpectedly failed"
  else
    (( actual_status != 0 )) || fail "$name unexpectedly succeeded"
  fi

  [[ -f "$state/deleted" ]] || fail "$name did not delete its VM"
  grep -F 'launch' "$state/commands.log" >/dev/null ||
    fail "$name did not launch a VM"
  grep -F 'delete --purge' "$state/commands.log" >/dev/null ||
    fail "$name did not request targeted purge"
  find "$output" -path '*/artifacts/example-job/status.json' -type f | grep -q . ||
    fail "$name did not retrieve artifacts"

  if (( expected_status != 0 )); then
    find "$output" -path '*/work/failure.log' -type f | grep -q . ||
      fail "$name did not retrieve failure diagnostics"
  fi
}

run_case success 0
run_case failure 17

# A saved configuration is validated, resolves paths relative to itself, and
# accepts explicit scalar overrides and repeatable additions.
config_dir="$TEMP/config"
config_state="$TEMP/config-state"
mkdir -p "$config_dir" "$config_state"
: >"$config_dir/config-installer.exe"
"$ROOT/bin/winebundle-vm" config create "$config_dir/build.json" \
  --installer ./config-installer.exe \
  --entrypoint 'C:\Config App\app.exe' \
  --output ./artifacts \
  --install-arg SILENT \
  --timeout 120 \
  --skip-provision >/dev/null || fail 'config create failed'
"$ROOT/bin/winebundle-vm" config validate "$config_dir/build.json" >/dev/null ||
  fail 'config validation failed'
FAKE_MULTIPASS_STATE="$config_state" \
  MULTIPASS_COMMAND="$ROOT/tests/fixtures/fake-multipass" \
  "$ROOT/bin/winebundle-vm" run \
    --config "$config_dir/build.json" \
    --timeout 240 \
    --install-arg NORESTART >/dev/null 2>&1 || fail 'config build failed'
grep -F -- '--timeout 240' "$config_state/commands.log" >/dev/null ||
  fail 'explicit scalar option did not override config'
grep -F -- '--install-arg SILENT' "$config_state/commands.log" >/dev/null ||
  fail 'config repeatable option was not forwarded'
grep -F -- '--install-arg NORESTART' "$config_state/commands.log" >/dev/null ||
  fail 'explicit repeatable option was not appended'

# Hooks and validation options reach the guest command line.
hooks_state="$TEMP/hooks-state"
mkdir -p "$hooks_state"
printf '#!/usr/bin/env bash\n' >"$TEMP/pre.sh"
FAKE_MULTIPASS_STATE="$hooks_state" \
  MULTIPASS_COMMAND="$ROOT/tests/fixtures/fake-multipass" \
  "$ROOT/bin/winebundle-vm" run \
    --installer "$TEMP/installer.exe" \
    --entrypoint 'C:\Program Files\Test App\app.exe' \
    --winetricks corefonts --winetricks vcrun2019 \
    --pre-install-script "$TEMP/pre.sh" \
    --smoke-window 'Main Window' \
    --keep-host-drive \
    --output "$TEMP/hooks-output" \
    --skip-provision >/dev/null 2>&1 || fail 'hooks case failed'
for expected in '--winetricks corefonts' '--winetricks vcrun2019' \
  '--pre-install-script' '--smoke-window Main\ Window' '--keep-host-drive'; do
  grep -F -- "$expected" "$hooks_state/commands.log" >/dev/null ||
    fail "guest command is missing: $expected"
done
grep -F 'pre-install-input.sh' "$hooks_state/commands.log" >/dev/null ||
  fail 'pre-install script was not transferred'

if "$ROOT/bin/winebundle-vm" run --installer "$TEMP/installer.exe" \
  --entrypoint 'C:\app.exe' --winetricks '--evil' --output "$TEMP/bad" \
  >/dev/null 2>&1; then
  fail 'option-like winetricks verb was accepted'
fi

# An archive from the VM must not be able to write outside the output directory.
for mode in traversal symlink; do
  state="$TEMP/$mode-state"
  mkdir -p "$state/outside"
  if FAKE_MULTIPASS_STATE="$state" FAKE_MULTIPASS_MALICIOUS="$mode" \
    MULTIPASS_COMMAND="$ROOT/tests/fixtures/fake-multipass" \
    "$ROOT/bin/winebundle-vm" run \
      --installer "$TEMP/installer.exe" \
      --entrypoint 'C:\Program Files\Test App\app.exe' \
      --output "$TEMP/$mode-output" \
      --skip-provision >/dev/null 2>&1; then
    fail "$mode archive was accepted"
  fi
  [[ -f "$state/deleted" ]] || fail "$mode case did not delete its VM"
  [[ ! -e "$state/outside/pwn.txt" ]] || fail "$mode archive wrote through a symlink"
  [[ ! -e "$TEMP/escaped.txt" && ! -e "$TEMP/$mode-output/escaped.txt" ]] ||
    fail "$mode archive escaped the output directory"
done

printf 'PASS: Multipass lifecycle tests\n'
