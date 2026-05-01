#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PID_FILE="$ROOT/platform/demo/runtime/pids.txt"
TAIL_PID_FILE="$ROOT/platform/demo/runtime/tail-pids.txt"

cleanup_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill -9 >/dev/null 2>&1 || true
  fi
}

if [[ ! -f "$PID_FILE" ]]; then
  if [[ -f "$TAIL_PID_FILE" ]]; then
    while IFS= read -r pid; do
      [[ -n "$pid" ]] || continue
      kill "$pid" >/dev/null 2>&1 || true
      kill -9 "$pid" >/dev/null 2>&1 || true
    done <"$TAIL_PID_FILE"
    rm -f "$TAIL_PID_FILE"
  fi
  for port in 3000 8010 8110 8210 9002 9005 9006 9007 9015 9016 9017 9101; do
    cleanup_port "$port"
  done
  echo "No demo PID file found. Cleaned known demo ports."
  exit 0
fi

entries=()
while IFS= read -r line; do
  entries+=("$line")
done <"$PID_FILE"
for (( idx=${#entries[@]}-1 ; idx>=0 ; idx-- )) ; do
  IFS=: read -r name pid <<<"${entries[$idx]}"
  if kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    sleep 0.2
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
done

rm -f "$PID_FILE"
if [[ -f "$TAIL_PID_FILE" ]]; then
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    kill "$pid" >/dev/null 2>&1 || true
    kill -9 "$pid" >/dev/null 2>&1 || true
  done <"$TAIL_PID_FILE"
  rm -f "$TAIL_PID_FILE"
fi
for port in 3000 8010 8110 8210 9002 9005 9006 9007 9015 9016 9017 9101; do
  cleanup_port "$port"
done
echo "Stopped demo processes."
