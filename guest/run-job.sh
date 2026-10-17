#!/usr/bin/env bash
set -euo pipefail

job_root='' installer='' entrypoint='' smoke_script='' pre_install_script='' smoke_window=''
arch='win64' timeout_seconds=180 smoke_exit=0 provision=1 keep_host_drive=0
declare -a install_args=() smoke_args=() winetricks_verbs=()

die() {
  printf 'winebundle guest: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --job-root) job_root=$2; shift 2 ;;
    --installer) installer=$2; shift 2 ;;
    --entrypoint) entrypoint=$2; shift 2 ;;
    --smoke-script) smoke_script=$2; shift 2 ;;
    --smoke-window) smoke_window=$2; shift 2 ;;
    --smoke-exit-code) smoke_exit=$2; shift 2 ;;
    --pre-install-script) pre_install_script=$2; shift 2 ;;
    --install-arg) install_args+=("$2"); shift 2 ;;
    --smoke-arg) smoke_args+=("$2"); shift 2 ;;
    --winetricks) winetricks_verbs+=("$2"); shift 2 ;;
    --keep-host-drive) keep_host_drive=1; shift ;;
    --arch) arch=$2; shift 2 ;;
    --timeout) timeout_seconds=$2; shift 2 ;;
    --skip-provision) provision=0; shift ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -n "$job_root" && -d "$job_root" ]] || die "job root is missing"
[[ -f "$installer" ]] || die "installer is missing"
[[ -n "$entrypoint" ]] || die "entrypoint is missing"

mkdir -p "$job_root/artifacts" "$job_root/work"
package_results() {
  local status=$?
  tar -C "$job_root" -czf "$job_root/result.tar.gz" artifacts work \
    >"$job_root/package.log" 2>&1 || {
      printf 'winebundle guest: failed to package results\n' >&2
      return "$status"
    }
  return "$status"
}
trap package_results EXIT

if (( provision )); then
  sudo dpkg --add-architecture i386
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    wine64 wine32 xvfb x11-utils winetricks cabextract tar coreutils
fi

tar -C "$job_root" -xzf "$job_root/worker.tar.gz"
chmod +x "$job_root/worker/winebundle"

declare -a command=(
  xvfb-run -a "$job_root/worker/winebundle" build
  --installer "$installer"
  --entrypoint "$entrypoint"
  --output "$job_root/artifacts"
  --work-root "$job_root/work"
  --arch "$arch"
  --timeout "$timeout_seconds"
  --runner-id "$(wine --version)"
  --network external
)
[[ -n "$smoke_window" ]] || command+=(--smoke-exit-code "$smoke_exit")
[[ -z "$smoke_script" ]] || command+=(--smoke-script "$smoke_script")
[[ -z "$pre_install_script" ]] || command+=(--pre-install-script "$pre_install_script")
[[ -z "$smoke_window" ]] || command+=(--smoke-window "$smoke_window")
(( ! keep_host_drive )) || command+=(--keep-host-drive)
for value in "${install_args[@]}"; do command+=(--install-arg "$value"); done
for value in "${smoke_args[@]}"; do command+=(--smoke-arg "$value"); done
for value in "${winetricks_verbs[@]}"; do command+=(--winetricks "$value"); done

"${command[@]}"
