#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT/platform/operator/runtime"
PID_FILE="$RUNTIME_DIR/pids.txt"

cleanup_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill -9 >/dev/null 2>&1 || true
  fi
}

if [[ -f "$PID_FILE" ]]; then
  entries=()
  while IFS= read -r line; do
    entries+=("$line")
  done <"$PID_FILE"
  for (( idx=${#entries[@]}-1 ; idx>=0 ; idx-- )) ; do
    IFS=: read -r _ pid <<<"${entries[$idx]}"
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      sleep 0.2
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done
  rm -f "$PID_FILE"
fi

for port in 9005 9006 8110 9101; do
  cleanup_port "$port"
done

echo "Stopped operator worker processes."
