#!/usr/bin/env bash

MULTIPASS_COMMAND=${MULTIPASS_COMMAND:-multipass}

multipass_require() {
  require_command "$MULTIPASS_COMMAND"
}

multipass_launch() {
  local image=$1 name=$2 cpus=$3 memory=$4 disk=$5
  "$MULTIPASS_COMMAND" launch "$image" \
    --name "$name" \
    --cpus "$cpus" \
    --memory "$memory" \
    --disk "$disk"
}

multipass_exec() {
  local name=$1
  shift
  "$MULTIPASS_COMMAND" exec "$name" -- "$@"
}

multipass_transfer_in() {
  local source=$1 name=$2 destination=$3
  "$MULTIPASS_COMMAND" transfer "$source" "$name:$destination"
}

multipass_transfer_out() {
  local name=$1 source=$2 destination=$3
  "$MULTIPASS_COMMAND" transfer "$name:$source" "$destination"
}

multipass_delete() {
  "$MULTIPASS_COMMAND" delete --purge "$1"
}
