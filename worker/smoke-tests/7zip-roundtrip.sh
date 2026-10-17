#!/usr/bin/env bash
set -euo pipefail

: "${WINEPREFIX:?WINEPREFIX is required}"
: "${WINE_COMMAND:?WINE_COMMAND is required}"
: "${ENTRYPOINT:?ENTRYPOINT is required}"
: "${SMOKE_WORK_DIR:?SMOKE_WORK_DIR is required}"

rm -rf "$SMOKE_WORK_DIR"
mkdir -p "$SMOKE_WORK_DIR/source" "$SMOKE_WORK_DIR/extracted"
printf 'winebundle 7-Zip round-trip fixture\n' >"$SMOKE_WORK_DIR/source/input.txt"

"$WINE_COMMAND" "$ENTRYPOINT" a -t7z \
  'C:\winebundle-smoke\fixture.7z' \
  'C:\winebundle-smoke\source\input.txt'
"$WINE_COMMAND" "$ENTRYPOINT" t 'C:\winebundle-smoke\fixture.7z'
"$WINE_COMMAND" "$ENTRYPOINT" x -y \
  '-oC:\winebundle-smoke\extracted' \
  'C:\winebundle-smoke\fixture.7z'

cmp "$SMOKE_WORK_DIR/source/input.txt" "$SMOKE_WORK_DIR/extracted/input.txt"
