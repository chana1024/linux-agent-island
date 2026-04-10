#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

backend_pid=""
log_level="INFO"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --log-level)
      if [[ $# -lt 2 ]]; then
        echo "missing value for --log-level" >&2
        exit 1
      fi
      log_level="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

cleanup() {
  if [[ -n "$backend_pid" ]] && kill -0 "$backend_pid" >/dev/null 2>&1; then
    kill "$backend_pid" >/dev/null 2>&1 || true
    wait "$backend_pid" 2>/dev/null || true
  fi
}

trap cleanup EXIT

/usr/bin/python3 -m linux_agent_island.backend --log-level "$log_level" &
backend_pid=$!

sleep 0.5

/usr/bin/python3 -m linux_agent_island.frontend --log-level "$log_level"
