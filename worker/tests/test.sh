#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_file() {
  [[ -f "$1" ]] || fail "expected file: $1"
}

chmod +x "$ROOT/winebundle" "$ROOT/tests/fixtures/fake-wine" \
  "$ROOT/tests/fixtures/fake-wineboot" "$ROOT/tests/fixtures/fake-wineserver" \
  "$ROOT/tests/fixtures/fake-smoke" "$ROOT/tests/fixtures/fake-winetricks" \
  "$ROOT/tests/fixtures/fake-xwininfo" "$ROOT/tests/fixtures/fake-preinstall"

installer="$TMP_ROOT/installer.exe"
: >"$installer"
output="$TMP_ROOT/output"

fake_args=(
  --runner-id wine-fake-1.0
  --wine "$ROOT/tests/fixtures/fake-wine"
  --wineboot "$ROOT/tests/fixtures/fake-wineboot"
  --wineserver "$ROOT/tests/fixtures/fake-wineserver"
)
app='C:\Program Files\Test App\app.exe'

first_bundle() {
  find "$1" -mindepth 1 -maxdepth 1 -type d | head -n 1
}

"$ROOT/winebundle" build \
  --installer "$installer" \
  --install-arg /S \
  --entrypoint "$app" \
  --smoke-arg --self-test \
  --output "$output" \
  "${fake_args[@]}" \
  --work-root "$TMP_ROOT/work"

bundle="$(first_bundle "$output")"
[[ -n "$bundle" ]] || fail 'bundle directory was not created'
assert_file "$bundle/manifest.json"
assert_file "$bundle/prefix.tar.gz"
assert_file "$bundle/SHA256SUMS"
assert_file "$bundle/build.log"
assert_file "$bundle/validation.log"
assert_file "$bundle/status.json"
assert_file "$bundle/run.sh"
[[ -x "$bundle/run.sh" ]] || fail 'launcher is not executable'

(cd "$bundle" && sha256sum --check SHA256SUMS >/dev/null) ||
  fail 'published checksums did not verify'
grep -F 'run.sh' "$bundle/SHA256SUMS" >/dev/null ||
  fail 'launcher is not covered by checksums'
grep -F '"classification": "supported"' "$bundle/manifest.json" >/dev/null ||
  fail 'manifest lacks supported classification'
grep -F '"launcher": "run.sh"' "$bundle/manifest.json" >/dev/null ||
  fail 'manifest does not name the launcher'
grep -F '"hostDriveMapped": false' "$bundle/manifest.json" >/dev/null ||
  fail 'manifest does not record the removed host drive'
grep -F '"state": "succeeded"' "$bundle/status.json" >/dev/null ||
  fail 'status is not succeeded'

# The bundle must not depend on the build machine's filesystem.
members="$(tar -tvzf "$bundle/prefix.tar.gz")"
grep -E 'dosdevices/z:' <<<"$members" >/dev/null &&
  fail 'bundle still maps the host filesystem as z:'
grep -E 'dosdevices/c:' <<<"$members" >/dev/null ||
  fail 'bundle lost the c: mapping'
grep -E -- '-> /' <<<"$members" >/dev/null &&
  fail 'bundle still contains absolute symlinks'
grep -E 'users/tester/Documents/? *$' <<<"$members" >/dev/null ||
  fail 'bundle lost the user Documents folder'

# The launcher extracts the prefix once and runs the entrypoint with matching settings.
launch_prefix="$TMP_ROOT/launch/prefix"
record="$TMP_ROOT/wine-record"
WINE="$ROOT/tests/fixtures/fake-wine" WINEBUNDLE_PREFIX_DIR="$launch_prefix" \
  FAKE_WINE_RECORD="$record" "$bundle/run.sh" --first --second ||
  fail 'launcher failed'
assert_file "$launch_prefix/.winebundle-ready"
assert_file "$launch_prefix/drive_c/Program Files/Test App/app.exe"
grep -F "WINEPREFIX=$launch_prefix WINEARCH=win64 ARGS=$app --first --second" "$record" >/dev/null ||
  fail "launcher did not run the entrypoint with the prefix: $(cat "$record")"
touch "$launch_prefix/user-data"
WINE="$ROOT/tests/fixtures/fake-wine" WINEBUNDLE_PREFIX_DIR="$launch_prefix" \
  "$bundle/run.sh" >/dev/null || fail 'second launch failed'
assert_file "$launch_prefix/user-data"

mkdir -p "$TMP_ROOT/occupied"
: >"$TMP_ROOT/occupied/file"
if WINE="$ROOT/tests/fixtures/fake-wine" WINEBUNDLE_PREFIX_DIR="$TMP_ROOT/occupied" \
  "$bundle/run.sh" >/dev/null 2>&1; then
  fail 'launcher overwrote a directory that is not a winebundle prefix'
fi
assert_file "$TMP_ROOT/occupied/file"

echo tampered >>"$bundle/prefix.tar.gz"
if WINE="$ROOT/tests/fixtures/fake-wine" WINEBUNDLE_PREFIX_DIR="$TMP_ROOT/tampered" \
  "$bundle/run.sh" >/dev/null 2>&1; then
  fail 'launcher accepted a bundle that fails checksum verification'
fi

FAKE_SMOKE_EXIT=7 "$ROOT/winebundle" build \
  --installer "$installer" \
  --entrypoint "$app" \
  --smoke-exit-code 7 \
  --output "$TMP_ROOT/nonzero-output" \
  "${fake_args[@]}" \
  --work-root "$TMP_ROOT/nonzero-work" >/dev/null ||
  fail 'expected nonzero smoke-test exit code was rejected'

"$ROOT/winebundle" build \
  --installer "$installer" \
  --entrypoint "$app" \
  --smoke-script "$ROOT/tests/fixtures/fake-smoke" \
  --output "$TMP_ROOT/script-output" \
  "${fake_args[@]}" \
  --work-root "$TMP_ROOT/script-work" >/dev/null ||
  fail 'script smoke test was rejected'
script_bundle="$(first_bundle "$TMP_ROOT/script-output")"
assert_file "$script_bundle/smoke-test.sh"
(cd "$script_bundle" && sha256sum --check SHA256SUMS >/dev/null) ||
  fail 'script smoke test was not covered by published checksums'

# --keep-host-drive preserves the z: mapping and says so in the manifest.
"$ROOT/winebundle" build \
  --installer "$installer" \
  --entrypoint "$app" \
  --keep-host-drive \
  --output "$TMP_ROOT/keep-output" \
  "${fake_args[@]}" \
  --work-root "$TMP_ROOT/keep-work" >/dev/null ||
  fail 'build with --keep-host-drive failed'
keep_bundle="$(first_bundle "$TMP_ROOT/keep-output")"
tar -tvzf "$keep_bundle/prefix.tar.gz" | grep -E 'dosdevices/z:' >/dev/null ||
  fail '--keep-host-drive dropped the z: mapping'
grep -F '"hostDriveMapped": true' "$keep_bundle/manifest.json" >/dev/null ||
  fail 'manifest does not record the kept host drive'

# winetricks verbs and the pre-install script run in the build prefix and are recorded.
"$ROOT/winebundle" build \
  --installer "$installer" \
  --entrypoint "$app" \
  --winetricks corefonts --winetricks vcrun2019 \
  --winetricks-command "$ROOT/tests/fixtures/fake-winetricks" \
  --pre-install-script "$ROOT/tests/fixtures/fake-preinstall" \
  --output "$TMP_ROOT/hooks-output" \
  "${fake_args[@]}" \
  --work-root "$TMP_ROOT/hooks-work" >/dev/null ||
  fail 'build with winetricks and a pre-install script failed'
hooks_bundle="$(first_bundle "$TMP_ROOT/hooks-output")"
mkdir "$TMP_ROOT/hooks-prefix"
tar -C "$TMP_ROOT/hooks-prefix" -xzf "$hooks_bundle/prefix.tar.gz"
grep -Fx -- '-q corefonts vcrun2019' "$TMP_ROOT/hooks-prefix/drive_c/winetricks-args" >/dev/null ||
  fail 'winetricks did not receive the requested verbs'
assert_file "$TMP_ROOT/hooks-prefix/drive_c/pre-install-ran"
assert_file "$hooks_bundle/pre-install.sh"
grep -F '"winetricks": ["corefonts","vcrun2019"]' "$hooks_bundle/manifest.json" >/dev/null ||
  fail 'manifest does not record winetricks verbs'
grep -F '"preInstallScript": {"name": "pre-install.sh"' "$hooks_bundle/manifest.json" >/dev/null ||
  fail 'manifest does not record the pre-install script'
(cd "$hooks_bundle" && sha256sum --check SHA256SUMS | grep -F pre-install.sh >/dev/null) ||
  fail 'pre-install script is not covered by checksums'

if "$ROOT/winebundle" build --installer "$installer" --entrypoint "$app" \
  --winetricks '--evil' --output "$TMP_ROOT/bad-verb" "${fake_args[@]}" >/dev/null 2>&1; then
  fail 'option-like winetricks verb was accepted'
fi

# A window smoke test passes only when a matching window appears.
FAKE_WINDOW_TITLE='Test App Main Window' "$ROOT/winebundle" build \
  --installer "$installer" \
  --entrypoint "$app" \
  --smoke-window 'Main Window' \
  --xwininfo-command "$ROOT/tests/fixtures/fake-xwininfo" \
  --timeout 10 \
  --output "$TMP_ROOT/window-output" \
  "${fake_args[@]}" \
  --work-root "$TMP_ROOT/window-work" >/dev/null ||
  fail 'window smoke test was rejected'
window_bundle="$(first_bundle "$TMP_ROOT/window-output")"
grep -F '"type": "window"' "$window_bundle/manifest.json" >/dev/null ||
  fail 'manifest does not record the window smoke test'
grep -F 'window matched: Main Window' "$window_bundle/validation.log" >/dev/null ||
  fail 'validation log does not record the matched window'

if FAKE_WINDOW_TITLE='Something Else' "$ROOT/winebundle" build \
  --installer "$installer" \
  --entrypoint "$app" \
  --smoke-window 'Main Window' \
  --xwininfo-command "$ROOT/tests/fixtures/fake-xwininfo" \
  --timeout 3 \
  --output "$TMP_ROOT/nowindow-output" \
  "${fake_args[@]}" \
  --work-root "$TMP_ROOT/nowindow-work" >/dev/null 2>&1; then
  fail 'window smoke test passed without a matching window'
fi

# A timed-out installer must not leave Wine processes behind.
server_log="$TMP_ROOT/wineserver.log"
if FAKE_WINE_HANG=1 FAKE_WINESERVER_LOG="$server_log" "$ROOT/winebundle" build \
  --installer "$installer" \
  --entrypoint "$app" \
  --timeout 1 \
  --output "$TMP_ROOT/hang-output" \
  "${fake_args[@]}" \
  --work-root "$TMP_ROOT/hang-work" >/dev/null 2>&1; then
  fail 'hung installer was accepted'
fi
grep -Fx -- '-k' "$server_log" >/dev/null ||
  fail 'timeout did not terminate leftover Wine processes'

if "$ROOT/winebundle" build --installer "$TMP_ROOT/missing.exe" \
  --entrypoint 'C:\app.exe' --output "$output" >/dev/null 2>&1; then
  fail 'missing installer was accepted'
fi

printf 'PASS: winebundle local contract tests\n'
