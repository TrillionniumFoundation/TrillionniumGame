#!/usr/bin/env bash
set -euo pipefail

if (( $# != 1 )); then
  echo "usage: $0 COMPOSE_ENV_FILE" >&2
  exit 64
fi

env_file=$1
if [[ ! -f "$env_file" || -L "$env_file" ]]; then
  echo "ERROR: Compose env input must be a regular non-symlink file" >&2
  exit 64
fi
if [[ -v TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS ]]; then
  echo "ERROR: obsolete TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS is forbidden in the Compose process environment" >&2
  exit 65
fi

obsolete_line=$(awk '
  /^[[:space:]]*(export[[:space:]]+)?TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS[[:space:]]*=/ {
    print FNR
    exit
  }
' "$env_file")
if [[ -n "$obsolete_line" ]]; then
  echo "ERROR: obsolete TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS is forbidden in the Compose env file (line $obsolete_line)" >&2
  exit 65
fi

echo "Nakama Compose env lint: no obsolete authority private-key ring"
