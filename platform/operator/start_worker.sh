#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT/platform/operator/runtime"
LOG_DIR="$RUNTIME_DIR/logs"
PID_FILE="$RUNTIME_DIR/pids.txt"
VENV_DIR="$ROOT/.venv"
WORKER_ENV_FILE="$RUNTIME_DIR/worker.env"
NODE_CONFIG_PATH="$RUNTIME_DIR/worker-node.json"
NEXUS_DIR="$ROOT/node-nexus-agent"
LOG_PREFIX="operator-worker"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

wait_for_http() {
  local url="$1"
  local attempts="${2:-60}"
  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for $url" >&2
  return 1
}

start_process() {
  local name="$1"
  shift
  local log_file="$LOG_DIR/$name.log"
  : >"$log_file"
  {
    echo "===== $(date -u +"%Y-%m-%dT%H:%M:%SZ") $name session start ====="
  } >>"$log_file"
  nohup bash -lc "$*" >>"$log_file" 2>&1 </dev/null &
  local pid=$!
  echo "$name:$pid" >>"$PID_FILE"
}

stream_runtime_logs() {
  local files=(
    "$LOG_DIR/${LOG_PREFIX}-node.log"
    "$LOG_DIR/${LOG_PREFIX}-router.log"
    "$LOG_DIR/${LOG_PREFIX}-daemon.log"
  )

  if [[ "${NODEHUB_NODE_NEXUS_AGENT_ENABLED:-false}" == "true" ]]; then
    files+=("$LOG_DIR/${LOG_PREFIX}-nexus.log")
  fi

  echo ""
  echo "Streaming worker logs. Press Ctrl+C to stop following logs; the worker keeps running."
  exec tail -n 20 -F "${files[@]}"
}

print_log_tail() {
  local label="$1"
  local file="$2"
  local lines="${3:-80}"
  if [[ -f "$file" ]]; then
    echo ""
    echo "----- $label ($file) -----" >&2
    tail -n "$lines" "$file" >&2 || true
  fi
}

port_in_use() {
  local port="$1"
  lsof -ti tcp:"$port" >/dev/null 2>&1
}

primary_lan_ip() {
  local iface ip
  iface="$(route -n get default 2>/dev/null | awk '/interface:/ {print $2}')"
  if [[ -n "$iface" ]]; then
    ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
  fi
  if [[ -z "${ip:-}" ]]; then
    ip="$(ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2; exit}')"
  fi
  printf '%s' "${ip:-}"
}

probe_inbound_9101() {
  local lan_ip="$1"
  if [[ -z "$lan_ip" ]] || ! command -v nc >/dev/null 2>&1; then
    return 0
  fi
  if nc -G 2 -z "$lan_ip" 9101 >/dev/null 2>&1; then
    echo "[Start] Self-probe: $lan_ip:9101 is reachable."
    return 0
  fi
  echo "" >&2
  echo "[Start] WARNING: $lan_ip:9101 is not reachable from this host." >&2
  echo "  Other laptops will see 'Operation timed out' until you run:" >&2
  echo "    sudo $ROOT/platform/operator/configure_firewall.sh" >&2
  return 1
}

ensure_port_free() {
  local port="$1"
  if port_in_use "$port"; then
    echo "Port $port is already in use. Stop the conflicting process before starting the worker." >&2
    exit 1
  fi
}

require_cmd curl
require_cmd lsof
require_cmd npm

if [[ ! -f "$WORKER_ENV_FILE" ]]; then
  echo "No onboarded worker runtime found at $WORKER_ENV_FILE" >&2
  echo "Run ./OnboardWorker first on this machine." >&2
  exit 1
fi

if [[ ! -f "$NODE_CONFIG_PATH" ]]; then
  echo "Worker node config not found at $NODE_CONFIG_PATH" >&2
  echo "Run ./OnboardWorker again to regenerate the runtime." >&2
  exit 1
fi

if [[ ! -x "$ROOT/node" ]]; then
  echo "Missing ./node binary. Build it first with: make build" >&2
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Missing Python virtual environment at $VENV_DIR" >&2
  echo "Run ./OnboardWorker again to prepare the local worker runtime." >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$WORKER_ENV_FILE"
set +a

if [[ -f "$PID_FILE" ]]; then
  echo "[Start] Stopping previous operator worker processes."
  "$ROOT/platform/operator/stop_worker.sh" >/dev/null 2>&1 || true
fi

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"
: >"$PID_FILE"

for port in 9005 9006 8110 9101; do
  ensure_port_free "$port"
done

if [[ "${NODEHUB_NODE_NEXUS_AGENT_ENABLED:-false}" == "true" ]]; then
  ensure_port_free 8080
fi

start_process "${LOG_PREFIX}-node" "cd '$ROOT' && ./node -config '$NODE_CONFIG_PATH'"
echo "[Start] Starting local AXL node."
wait_for_http "http://127.0.0.1:9005/topology"

start_process "${LOG_PREFIX}-router" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && PYTHONPATH=integrations python -m mcp_routing.mcp_router --port 9006"
echo "[Start] Starting MCP router."
wait_for_http "http://127.0.0.1:9006/health"

if [[ "${NODEHUB_NODE_NEXUS_AGENT_ENABLED:-false}" == "true" ]]; then
  if [[ ! -d "$NEXUS_DIR" ]]; then
    echo "Browser runtime sources not found at $NEXUS_DIR" >&2
    exit 1
  fi
  start_process "${LOG_PREFIX}-nexus" "cd '$NEXUS_DIR' && set -a && source '$WORKER_ENV_FILE' && set +a && node src/server.js"
  echo "[Start] Starting NodeHub browser runtime."
  if ! wait_for_http "${NODEHUB_NODE_NEXUS_AGENT_URL:-http://127.0.0.1:8080}/health"; then
    print_log_tail "Browser runtime log" "$LOG_DIR/${LOG_PREFIX}-nexus.log"
    exit 1
  fi
fi

start_process "${LOG_PREFIX}-daemon" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && set -a && source '$WORKER_ENV_FILE' && set +a && PYTHONPATH=platform uvicorn daemon.app:app --host 127.0.0.1 --port 8110"
echo "[Start] Starting worker daemon."

echo "[Start] Waiting for worker daemon health."
if ! wait_for_http "http://127.0.0.1:8110/health"; then
  print_log_tail "Node log" "$LOG_DIR/${LOG_PREFIX}-node.log"
  print_log_tail "Router log" "$LOG_DIR/${LOG_PREFIX}-router.log"
  print_log_tail "Browser runtime log" "$LOG_DIR/${LOG_PREFIX}-nexus.log"
  print_log_tail "Daemon log" "$LOG_DIR/${LOG_PREFIX}-daemon.log"
  exit 1
fi

echo "[Start] Waiting for A2A agent card."
if ! wait_for_http "http://127.0.0.1:8110/.well-known/agent-card.json"; then
  print_log_tail "Daemon log" "$LOG_DIR/${LOG_PREFIX}-daemon.log"
  exit 1
fi

IDENTITY_JSON="$(curl -fsS http://127.0.0.1:8110/identity)"
TOPOLOGY_JSON="$(curl -fsS http://127.0.0.1:9005/topology)"
PEER_ID="$(python3 - <<PY
import json
print(json.loads("""$TOPOLOGY_JSON""")["our_public_key"])
PY
)"

WORKER_LABEL="$(python3 - <<PY
import json
print(json.loads("""$IDENTITY_JSON""").get("label", "Operator Worker"))
PY
)"
WORKER_REGION="$(python3 - <<PY
import json
print(json.loads("""$IDENTITY_JSON""").get("region", "unknown"))
PY
)"
PAYOUT_WALLET="$(python3 - <<PY
import json
payload = json.loads("""$IDENTITY_JSON""")
print(payload.get("payout_wallet_address") or payload.get("wallet_address") or "")
PY
)"

LAN_IP="$(primary_lan_ip)"
probe_inbound_9101 "$LAN_IP" || true

echo ""
echo "NodeHub worker is live."
echo "Label: $WORKER_LABEL"
echo "Region: $WORKER_REGION"
echo "Peer ID: $PEER_ID"
echo "Payout wallet: $PAYOUT_WALLET"
echo "Daemon: http://127.0.0.1:8110"
echo "AXL API: http://127.0.0.1:9005"
if [[ -n "$LAN_IP" ]]; then
  echo "Peer URI to share: tls://$LAN_IP:9101"
fi
if [[ "${NODEHUB_NODE_NEXUS_AGENT_ENABLED:-false}" == "true" ]]; then
  echo "Browser runtime: ${NODEHUB_NODE_NEXUS_AGENT_URL:-http://127.0.0.1:8080}"
fi
echo "Logs: $LOG_DIR"

stream_runtime_logs
