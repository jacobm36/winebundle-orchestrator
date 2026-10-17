#!/usr/bin/env bash

wb_usage() {
  cat <<'EOF'
Usage:
  winebundle build [options]

Required:
  --installer PATH           Trusted EXE or MSI installer
  --entrypoint WINDOWS_PATH  Installed executable, for example C:\Program Files\App\App.exe
  --output DIR               Directory in which to publish the bundle

Options:
  --install-arg VALUE        Installer argument; repeat for each argument
  --smoke-arg VALUE          Entrypoint smoke-test argument; repeat for each argument
  --smoke-script PATH        Trusted Bash smoke test for multi-step validation
  --smoke-window TITLE       Pass when a window whose title contains TITLE appears
  --smoke-exit-code CODE     Expected smoke-test exit code (default: 0)
  --winetricks VERB          Install a winetricks verb before the installer; repeat as needed
  --pre-install-script PATH  Trusted Bash script run against the build prefix before the installer
  --keep-host-drive          Keep the z: drive and other host device mappings in the bundle
  --timeout SECONDS          Per-process timeout (default: 120)
  --arch win32|win64         Wine prefix architecture (default: win64)
  --runner-id VALUE          Reproducible Wine runner identifier (default: wine --version)
  --network enabled|external Network policy declaration (default: external)
  --wine PATH                Wine command (default: wine)
  --wineboot PATH            wineboot command (default: wineboot)
  --wineserver PATH          wineserver command (default: wineserver)
  --timeout-command PATH     Timeout command (default: timeout)
  --winetricks-command PATH  winetricks command (default: winetricks)
  --xwininfo-command PATH    xwininfo command used by --smoke-window (default: xwininfo)
  --work-root DIR            Parent for temporary job directories
  --keep-work                Retain working data after success
  --help                     Show this help

The published bundle contains run.sh, a launcher that extracts the prefix on
first use and starts the entrypoint with the matching Wine settings.

The pre-install script runs with WINEPREFIX, WINEARCH, WINE_COMMAND,
WINEBOOT_COMMAND, WINESERVER_COMMAND and INSTALLER set.

The build is synchronous. Network isolation and VM/container boundaries must be
enforced by the caller; this tool records but does not claim to enforce them.
EOF
}

wb_die() {
  printf 'winebundle: %s\n' "$*" >&2
  exit 1
}

wb_require_command() {
  command -v "$1" >/dev/null 2>&1 || wb_die "required command not found: $1"
}

wb_json_string() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  value=${value//$'\t'/\\t}
  printf '"%s"' "$value"
}

wb_json_array() {
  local first=1 value
  printf '['
  for value in "$@"; do
    if (( first )); then
      first=0
    else
      printf ','
    fi
    wb_json_string "$value"
  done
  printf ']'
}

wb_sha256() {
  sha256sum "$1" | awk '{print $1}'
}

wb_windows_c_path_to_prefix_path() {
  local prefix=$1 windows_path=$2 relative
  [[ "$windows_path" =~ ^[Cc]:[\\/](.+)$ ]] ||
    wb_die "entrypoint must be an absolute C: path"
  relative=${BASH_REMATCH[1]//\\//}
  printf '%s/drive_c/%s\n' "$prefix" "$relative"
}

wb_run_logged() {
  local log_file=$1 timeout_seconds=$2 timeout_command=$3
  shift 3
  {
    printf 'command:'
    printf ' %q' "$@"
    printf '\n'
  } >>"$log_file"

  local status
  if "$timeout_command" "$timeout_seconds" "$@" >>"$log_file" 2>&1; then
    status=0
  else
    status=$?
  fi
  printf 'exit_code: %s\n' "$status" >>"$log_file"
  # A timeout kills only the foreground process; wineserver and its children survive.
  if (( status == 124 || status == 137 )) && [[ -n "${WB_WINESERVER_COMMAND:-}" ]]; then
    printf 'timeout: terminating leftover Wine processes\n' >>"$log_file"
    "$WB_WINESERVER_COMMAND" -k >>"$log_file" 2>&1 || true
  fi
  return "$status"
}

wb_run_window_smoke() {
  local log_file=$1 timeout_seconds=$2 timeout_command=$3
  local wineserver_command=$4 xwininfo_command=$5 title=$6
  shift 6
  {
    printf 'command (window smoke, title %q):' "$title"
    printf ' %q' "$@"
    printf '\n'
  } >>"$log_file"

  "$timeout_command" "$timeout_seconds" "$@" >>"$log_file" 2>&1 &
  local pid=$! waited=0 found=1 tree=''
  while (( waited < timeout_seconds )); do
    if tree="$("$xwininfo_command" -root -tree 2>/dev/null)" &&
      grep -Fq -- "$title" <<<"$tree"; then
      found=0
      printf 'window matched: %s\n%s\n' "$title" "$tree" >>"$log_file"
      break
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      printf 'process exited before a matching window appeared\n' >>"$log_file"
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  (( found == 0 )) || printf 'no window matching %s appeared\n' "$title" >>"$log_file"

  kill "$pid" 2>/dev/null || true
  "$wineserver_command" -k >>"$log_file" 2>&1 || true
  wait "$pid" 2>/dev/null || true
  return "$found"
}

# Replace host-specific links so the prefix does not depend on the build machine.
wb_sanitize_prefix() {
  local prefix=$1 keep_host_drive=$2 link
  if (( ! keep_host_drive )) && [[ -d "$prefix/dosdevices" ]]; then
    while IFS= read -r -d '' link; do
      if [[ "$(readlink "$link")" == /* ]]; then
        rm -f "$link"
      fi
    done < <(find "$prefix/dosdevices" -maxdepth 1 -type l -print0)
  fi
  if [[ -d "$prefix/drive_c/users" ]]; then
    while IFS= read -r -d '' link; do
      if [[ "$(readlink "$link")" == /* ]]; then
        rm -f "$link"
        mkdir -p "$link"
      fi
    done < <(find "$prefix/drive_c/users" -type l -print0)
  fi
}

wb_find_host_links() {
  local prefix=$1 keep_host_drive=$2 link
  while IFS= read -r -d '' link; do
    if [[ "$(readlink "$link")" == /* ]]; then
      printf '%s\n' "${link#"$prefix"/}"
    fi
  done < <(
    if [[ -d "$prefix/drive_c/users" ]]; then
      find "$prefix/drive_c/users" -type l -print0
    fi
    if (( ! keep_host_drive )) && [[ -d "$prefix/dosdevices" ]]; then
      find "$prefix/dosdevices" -maxdepth 1 -type l -print0
    fi
  )
}

wb_write_launcher() {
  local path=$1 job_id=$2 entrypoint=$3 arch=$4 runner_id=$5
  {
    printf '#!/usr/bin/env bash\n'
    printf '# Launcher generated by winebundle. Set WINE to choose a Wine binary and\n'
    printf '# WINEBUNDLE_PREFIX_DIR to choose where the prefix is extracted.\n'
    printf 'set -euo pipefail\n\n'
    printf 'JOB_ID=%q\nENTRYPOINT=%q\nARCH=%q\nRUNNER_ID=%q\n' \
      "$job_id" "$entrypoint" "$arch" "$runner_id"
    cat <<'EOF'

die() {
  printf 'winebundle launcher: %s\n' "$*" >&2
  exit 1
}

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WINE_COMMAND=${WINE:-wine}
command -v "$WINE_COMMAND" >/dev/null 2>&1 ||
  die "Wine is required but was not found: $WINE_COMMAND"

current_runner="$("$WINE_COMMAND" --version 2>/dev/null || true)"
if [[ -n "$current_runner" && "$current_runner" != "$RUNNER_ID" ]]; then
  printf 'winebundle launcher: warning: bundle was built with %s but %s is installed\n' \
    "$RUNNER_ID" "$current_runner" >&2
fi

PREFIX_DIR=${WINEBUNDLE_PREFIX_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/winebundle/$JOB_ID}
if [[ ! -f "$PREFIX_DIR/.winebundle-ready" ]]; then
  (cd "$BUNDLE_DIR" && sha256sum --check --quiet SHA256SUMS) ||
    die "bundle checksum verification failed"
  if [[ -e "$PREFIX_DIR" ]]; then
    rmdir "$PREFIX_DIR" 2>/dev/null ||
      die "$PREFIX_DIR exists and is not a winebundle prefix"
  fi
  mkdir -p "$(dirname "$PREFIX_DIR")"
  partial="$PREFIX_DIR.partial.$$"
  rm -rf "$partial"
  mkdir -p "$partial"
  tar -C "$partial" -xzf "$BUNDLE_DIR/prefix.tar.gz" || {
    rm -rf "$partial"
    die "failed to extract prefix"
  }
  : >"$partial/.winebundle-ready"
  # A concurrent first launch may have finished already.
  if ! mv -T "$partial" "$PREFIX_DIR" 2>/dev/null; then
    rm -rf "$partial"
    [[ -f "$PREFIX_DIR/.winebundle-ready" ]] || die "failed to install prefix at $PREFIX_DIR"
  fi
fi

export WINEPREFIX="$PREFIX_DIR" WINEARCH="$ARCH"
export WINEDEBUG=${WINEDEBUG:--all}
exec "$WINE_COMMAND" "$ENTRYPOINT" "$@"
EOF
  } >"$path"
  chmod +x "$path"
}

wb_write_status() {
  local path=$1 state=$2 reason=${3:-}
  local temp="${path}.tmp"
  {
    printf '{\n  "state": '
    wb_json_string "$state"
    printf ',\n  "reason": '
    wb_json_string "$reason"
    printf '\n}\n'
  } >"$temp"
  mv "$temp" "$path"
}

wb_write_manifest() {
  local path=$1 job_id=$2 installer_name=$3 installer_sha=$4 entrypoint=$5
  local arch=$6 runner_id=$7 network=$8 timeout_seconds=$9
  local smoke_exit=${10} smoke_script_name=${11} smoke_script_sha=${12}
  local pre_install_name=${13} pre_install_sha=${14} smoke_window=${15}
  local host_drive_mapped=${16}
  local -n manifest_install_args=${17} manifest_smoke_args=${18}
  local -n manifest_winetricks=${19}

  {
    printf '{\n'
    printf '  "schemaVersion": 1,\n'
    printf '  "jobId": '; wb_json_string "$job_id"; printf ',\n'
    printf '  "installer": {"name": '; wb_json_string "$installer_name"
    printf ', "sha256": '; wb_json_string "$installer_sha"; printf '},\n'
    printf '  "wine": {"runnerId": '; wb_json_string "$runner_id"
    printf ', "architecture": '; wb_json_string "$arch"; printf '},\n'
    printf '  "entrypoint": '; wb_json_string "$entrypoint"; printf ',\n'
    printf '  "launcher": "run.sh",\n'
    printf '  "winetricks": '; wb_json_array "${manifest_winetricks[@]}"; printf ',\n'
    if [[ -n "$pre_install_name" ]]; then
      printf '  "preInstallScript": {"name": '; wb_json_string "$pre_install_name"
      printf ', "sha256": '; wb_json_string "$pre_install_sha"; printf '},\n'
    else
      printf '  "preInstallScript": null,\n'
    fi
    printf '  "installArguments": '; wb_json_array "${manifest_install_args[@]}"; printf ',\n'
    if [[ -n "$smoke_script_name" ]]; then
      printf '  "smokeTest": {"type": "script", "script": '
      wb_json_string "$smoke_script_name"
      printf ', "sha256": '; wb_json_string "$smoke_script_sha"
      printf ', "expectedExitCode": %s, "timeoutSeconds": %s},\n' "$smoke_exit" "$timeout_seconds"
    elif [[ -n "$smoke_window" ]]; then
      printf '  "smokeTest": {"type": "window", "title": '
      wb_json_string "$smoke_window"
      printf ', "arguments": '; wb_json_array "${manifest_smoke_args[@]}"
      printf ', "timeoutSeconds": %s},\n' "$timeout_seconds"
    else
      printf '  "smokeTest": {"type": "entrypoint", "arguments": '
      wb_json_array "${manifest_smoke_args[@]}"
      printf ', "expectedExitCode": %s, "timeoutSeconds": %s},\n' "$smoke_exit" "$timeout_seconds"
    fi
    printf '  "hostDriveMapped": %s,\n' "$host_drive_mapped"
    printf '  "networkPolicy": '; wb_json_string "$network"; printf ',\n'
    printf '  "compatibility": {"classification": "supported", "blockerCode": null}\n'
    printf '}\n'
  } >"$path"
}

wb_build() {
  local installer='' entrypoint='' output='' arch='win64'
  local runner_id='' network='external' timeout_seconds=120 smoke_exit=0
  local smoke_script='' pre_install_script='' smoke_window=''
  local wine_command='wine' wineboot_command='wineboot'
  local wineserver_command='wineserver' timeout_command='timeout'
  local winetricks_command='winetricks' xwininfo_command='xwininfo'
  local work_root="${TMPDIR:-/tmp}" keep_work=0 keep_host_drive=0 smoke_exit_set=0
  local -a install_args=() smoke_args=() winetricks_verbs=()

  while (($#)); do
    case "$1" in
      --installer) [[ $# -ge 2 ]] || wb_die "--installer requires a value"; installer=$2; shift 2 ;;
      --entrypoint) [[ $# -ge 2 ]] || wb_die "--entrypoint requires a value"; entrypoint=$2; shift 2 ;;
      --output) [[ $# -ge 2 ]] || wb_die "--output requires a value"; output=$2; shift 2 ;;
      --install-arg) [[ $# -ge 2 ]] || wb_die "--install-arg requires a value"; install_args+=("$2"); shift 2 ;;
      --smoke-arg) [[ $# -ge 2 ]] || wb_die "--smoke-arg requires a value"; smoke_args+=("$2"); shift 2 ;;
      --smoke-script) [[ $# -ge 2 ]] || wb_die "--smoke-script requires a value"; smoke_script=$2; shift 2 ;;
      --smoke-window) [[ $# -ge 2 ]] || wb_die "--smoke-window requires a value"; smoke_window=$2; shift 2 ;;
      --smoke-exit-code) [[ $# -ge 2 ]] || wb_die "--smoke-exit-code requires a value"; smoke_exit=$2; smoke_exit_set=1; shift 2 ;;
      --winetricks) [[ $# -ge 2 ]] || wb_die "--winetricks requires a value"; winetricks_verbs+=("$2"); shift 2 ;;
      --pre-install-script) [[ $# -ge 2 ]] || wb_die "--pre-install-script requires a value"; pre_install_script=$2; shift 2 ;;
      --keep-host-drive) keep_host_drive=1; shift ;;
      --timeout) [[ $# -ge 2 ]] || wb_die "--timeout requires a value"; timeout_seconds=$2; shift 2 ;;
      --arch) [[ $# -ge 2 ]] || wb_die "--arch requires a value"; arch=$2; shift 2 ;;
      --runner-id) [[ $# -ge 2 ]] || wb_die "--runner-id requires a value"; runner_id=$2; shift 2 ;;
      --network) [[ $# -ge 2 ]] || wb_die "--network requires a value"; network=$2; shift 2 ;;
      --wine) [[ $# -ge 2 ]] || wb_die "--wine requires a value"; wine_command=$2; shift 2 ;;
      --wineboot) [[ $# -ge 2 ]] || wb_die "--wineboot requires a value"; wineboot_command=$2; shift 2 ;;
      --wineserver) [[ $# -ge 2 ]] || wb_die "--wineserver requires a value"; wineserver_command=$2; shift 2 ;;
      --timeout-command) [[ $# -ge 2 ]] || wb_die "--timeout-command requires a value"; timeout_command=$2; shift 2 ;;
      --winetricks-command) [[ $# -ge 2 ]] || wb_die "--winetricks-command requires a value"; winetricks_command=$2; shift 2 ;;
      --xwininfo-command) [[ $# -ge 2 ]] || wb_die "--xwininfo-command requires a value"; xwininfo_command=$2; shift 2 ;;
      --work-root) [[ $# -ge 2 ]] || wb_die "--work-root requires a value"; work_root=$2; shift 2 ;;
      --keep-work) keep_work=1; shift ;;
      --help) wb_usage; return 0 ;;
      *) wb_die "unknown option: $1" ;;
    esac
  done

  [[ -n "$installer" ]] || wb_die "--installer is required"
  [[ -f "$installer" ]] || wb_die "installer does not exist: $installer"
  [[ "$installer" =~ \.(exe|EXE|msi|MSI)$ ]] || wb_die "installer must have an .exe or .msi extension"
  [[ -n "$entrypoint" ]] || wb_die "--entrypoint is required"
  [[ -n "$output" ]] || wb_die "--output is required"
  [[ -z "$smoke_script" || -f "$smoke_script" ]] ||
    wb_die "smoke script does not exist: $smoke_script"
  [[ -z "$smoke_script" || ${#smoke_args[@]} -eq 0 ]] ||
    wb_die "--smoke-script cannot be combined with --smoke-arg"
  [[ -z "$smoke_script" || -z "$smoke_window" ]] ||
    wb_die "--smoke-script cannot be combined with --smoke-window"
  [[ -z "$smoke_window" ]] || (( ! smoke_exit_set )) ||
    wb_die "--smoke-exit-code does not apply to --smoke-window"
  [[ -z "$pre_install_script" || -f "$pre_install_script" ]] ||
    wb_die "pre-install script does not exist: $pre_install_script"
  local verb
  for verb in "${winetricks_verbs[@]}"; do
    [[ "$verb" =~ ^[A-Za-z0-9][A-Za-z0-9_.=-]*$ ]] || wb_die "invalid winetricks verb: $verb"
  done
  [[ "$arch" == 'win32' || "$arch" == 'win64' ]] || wb_die "--arch must be win32 or win64"
  [[ "$network" == 'enabled' || "$network" == 'external' ]] ||
    wb_die "--network must be enabled or external"
  [[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || wb_die "--timeout must be a positive integer"
  [[ "$smoke_exit" =~ ^[0-9]+$ ]] || wb_die "--smoke-exit-code must be a non-negative integer"

  wb_require_command "$wine_command"
  wb_require_command "$wineboot_command"
  wb_require_command "$wineserver_command"
  wb_require_command "$timeout_command"
  wb_require_command tar
  wb_require_command sha256sum
  (( ${#winetricks_verbs[@]} == 0 )) || wb_require_command "$winetricks_command"
  [[ -z "$smoke_window" ]] || wb_require_command "$xwininfo_command"

  mkdir -p "$output" "$work_root"
  output="$(cd "$output" && pwd)"
  work_root="$(cd "$work_root" && pwd)"
  installer="$(cd "$(dirname "$installer")" && pwd)/$(basename "$installer")"
  if [[ -n "$smoke_script" ]]; then
    smoke_script="$(cd "$(dirname "$smoke_script")" && pwd)/$(basename "$smoke_script")"
  fi
  if [[ -n "$pre_install_script" ]]; then
    pre_install_script="$(cd "$(dirname "$pre_install_script")" && pwd)/$(basename "$pre_install_script")"
  fi

  local job_id installer_name installer_sha work_dir build_prefix validation_prefix
  local staging published status_file build_log validation_log entrypoint_file
  local smoke_script_name='' smoke_script_sha=''
  local pre_install_name='' pre_install_sha='' runner_label host_links
  job_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  installer_name="$(basename "$installer")"
  installer_sha="$(wb_sha256 "$installer")"
  work_dir="$work_root/winebundle-$job_id"
  build_prefix="$work_dir/build-prefix"
  validation_prefix="$work_dir/validation-prefix"
  staging="$work_dir/publish"
  published="$output/$job_id"
  status_file="$work_dir/status.json"
  build_log="$staging/build.log"
  validation_log="$staging/validation.log"
  mkdir -p "$build_prefix" "$staging"
  wb_write_status "$status_file" 'building'

  WB_WINESERVER_COMMAND=$wineserver_command
  export WINEARCH="$arch"
  export WINEPREFIX="$build_prefix"
  wb_run_logged "$build_log" "$timeout_seconds" "$timeout_command" "$wineboot_command" -u ||
    wb_die "wineboot failed; work retained at $work_dir"

  if (( ${#winetricks_verbs[@]} )); then
    WINE="$wine_command" wb_run_logged "$build_log" "$timeout_seconds" "$timeout_command" \
      "$winetricks_command" -q "${winetricks_verbs[@]}" ||
      wb_die "winetricks failed; work retained at $work_dir"
  fi

  if [[ -n "$pre_install_script" ]]; then
    pre_install_name='pre-install.sh'
    cp "$pre_install_script" "$staging/$pre_install_name"
    pre_install_sha="$(wb_sha256 "$staging/$pre_install_name")"
    wb_run_logged "$build_log" "$timeout_seconds" "$timeout_command" \
      env WINE_COMMAND="$wine_command" WINEBOOT_COMMAND="$wineboot_command" \
      WINESERVER_COMMAND="$wineserver_command" INSTALLER="$installer" \
      bash "$staging/$pre_install_name" ||
      wb_die "pre-install script failed; work retained at $work_dir"
  fi

  local -a install_command
  if [[ "$installer" =~ \.(msi|MSI)$ ]]; then
    install_command=("$wine_command" msiexec /i "$installer" "${install_args[@]}")
  else
    install_command=("$wine_command" "$installer" "${install_args[@]}")
  fi
  wb_run_logged "$build_log" "$timeout_seconds" "$timeout_command" "${install_command[@]}" ||
    wb_die "installer failed; work retained at $work_dir"
  wb_run_logged "$build_log" "$timeout_seconds" "$timeout_command" "$wineserver_command" -w ||
    wb_die "wineserver did not settle; work retained at $work_dir"

  entrypoint_file="$(wb_windows_c_path_to_prefix_path "$build_prefix" "$entrypoint")"
  [[ -f "$entrypoint_file" ]] ||
    wb_die "entrypoint was not installed at $entrypoint; work retained at $work_dir"

  wb_write_status "$status_file" 'bundling'
  wb_sanitize_prefix "$build_prefix" "$keep_host_drive"
  tar -C "$build_prefix" -czf "$staging/prefix.tar.gz" .
  if [[ -n "$smoke_script" ]]; then
    smoke_script_name='smoke-test.sh'
    cp "$smoke_script" "$staging/$smoke_script_name"
    smoke_script_sha="$(wb_sha256 "$staging/$smoke_script_name")"
  fi
  runner_label=${runner_id:-$("$wine_command" --version 2>/dev/null || printf unknown)}
  wb_write_launcher "$staging/run.sh" "$job_id" "$entrypoint" "$arch" "$runner_label"
  wb_write_manifest "$staging/manifest.json" "$job_id" "$installer_name" "$installer_sha" \
    "$entrypoint" "$arch" "$runner_label" \
    "$network" "$timeout_seconds" "$smoke_exit" "$smoke_script_name" "$smoke_script_sha" \
    "$pre_install_name" "$pre_install_sha" "$smoke_window" \
    "$( (( keep_host_drive )) && printf true || printf false )" \
    install_args smoke_args winetricks_verbs

  wb_write_status "$status_file" 'validating'
  mkdir -p "$validation_prefix"
  tar -C "$validation_prefix" -xzf "$staging/prefix.tar.gz"
  export WINEPREFIX="$validation_prefix"
  entrypoint_file="$(wb_windows_c_path_to_prefix_path "$validation_prefix" "$entrypoint")"
  [[ -f "$entrypoint_file" ]] ||
    wb_die "entrypoint missing after bundle import; work retained at $work_dir"
  host_links="$(wb_find_host_links "$validation_prefix" "$keep_host_drive")"
  if [[ -n "$host_links" ]]; then
    printf 'host-specific symlinks in bundle:\n%s\n' "$host_links" >>"$validation_log"
    wb_die "bundle contains host-specific symlinks (${host_links//$'\n'/, }); work retained at $work_dir"
  fi

  local actual_smoke_exit=0
  if [[ -n "$smoke_window" ]]; then
    wb_run_window_smoke "$validation_log" "$timeout_seconds" "$timeout_command" \
      "$wineserver_command" "$xwininfo_command" "$smoke_window" \
      "$wine_command" "$entrypoint" "${smoke_args[@]}" ||
      wb_die "no window matching '$smoke_window' appeared; work retained at $work_dir"
  else
    local -a smoke_command
    if [[ -n "$smoke_script_name" ]]; then
      export WINE_COMMAND="$wine_command"
      export ENTRYPOINT="$entrypoint"
      export SMOKE_WORK_DIR="$validation_prefix/drive_c/winebundle-smoke"
      smoke_command=(bash "$staging/$smoke_script_name")
    else
      smoke_command=("$wine_command" "$entrypoint" "${smoke_args[@]}")
    fi
    if wb_run_logged "$validation_log" "$timeout_seconds" "$timeout_command" \
      "${smoke_command[@]}"; then
      actual_smoke_exit=0
    else
      actual_smoke_exit=$?
    fi
    if [[ "$actual_smoke_exit" -ne "$smoke_exit" ]]; then
      wb_die "smoke test returned $actual_smoke_exit, expected $smoke_exit; work retained at $work_dir"
    fi
  fi

  (
    cd "$staging"
    local -a checksum_files=(manifest.json prefix.tar.gz run.sh build.log validation.log)
    [[ -z "$smoke_script_name" ]] || checksum_files+=("$smoke_script_name")
    [[ -z "$pre_install_name" ]] || checksum_files+=("$pre_install_name")
    sha256sum "${checksum_files[@]}" >SHA256SUMS
    sha256sum --check SHA256SUMS >/dev/null
  )
  wb_write_status "$status_file" 'publishing'
  [[ ! -e "$published" ]] || wb_die "publish destination already exists: $published"
  mv "$staging" "$published"
  (
    cd "$published"
    sha256sum --check SHA256SUMS >/dev/null
  ) || wb_die "published artifact verification failed: $published"

  wb_write_status "$published/status.json" 'succeeded'
  if (( keep_work )); then
    cp "$published/status.json" "$status_file"
    printf 'Bundle published: %s\nWork retained: %s\n' "$published" "$work_dir"
  else
    rm -rf "$work_dir"
    printf 'Bundle published: %s\n' "$published"
  fi
}

main() {
  local command=${1:-}
  case "$command" in
    build) shift; wb_build "$@" ;;
    --help|-h|help|'') wb_usage ;;
    *) wb_die "unknown command: $command" ;;
  esac
}
