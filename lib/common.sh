#!/usr/bin/env bash

log() {
  printf 'winebundle-vm: %s\n' "$*"
}

die() {
  printf 'winebundle-vm: %s\n' "$*" >&2
  exit 1
}

require_value() {
  [[ -n "$2" ]] || die "$1 requires a value"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

absolute_file() {
  local path=$1
  printf '%s/%s\n' "$(cd "$(dirname "$path")" && pwd)" "$(basename "$path")"
}

absolute_directory() {
  (cd "$1" && pwd)
}

# Extracts an archive produced by the VM, which is not trusted by the host.
# Rejects absolute or parent-relative paths, hard links, and members written
# beneath a symlink that the same archive creates (GNU tar listing format).
safe_extract_tar() {
  local archive=$1 destination=$2 names listing link
  names="$(tar -tzf "$archive")" || die "result archive is unreadable"
  names="$(sed 's,^\./,,' <<<"$names")"
  if grep -Eq '(^|/)\.\.(/|$)|^/' <<<"$names"; then
    die "result archive rejected: it contains absolute or parent-relative paths"
  fi
  listing="$(tar -tvzf "$archive")" || die "result archive is unreadable"
  if grep -q '^h' <<<"$listing"; then
    die "result archive rejected: it contains hard links"
  fi
  while IFS= read -r link; do
    [[ -n "$link" ]] || continue
    if awk -v prefix="$link/" 'index($0, prefix) == 1 { found = 1 } END { exit !found }' \
      <<<"$names"; then
      die "result archive rejected: it writes through the symlink $link"
    fi
  done < <(
    awk '/^l/ {
      sub(/^[^ ]+ +[^ ]+ +[^ ]+ +[^ ]+ +[^ ]+ /, "")
      i = index($0, " -> ")
      if (i > 0) print substr($0, 1, i - 1)
    }' <<<"$listing" | sed 's,^\./,,'
  )
  mkdir -p "$destination"
  tar -C "$destination" -xzf "$archive" --no-same-owner --no-same-permissions
}
