#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO_DIR="$ROOT/platform/demo"
RUNTIME_DIR="$DEMO_DIR/runtime"
LOG_DIR="$RUNTIME_DIR/logs"
PID_FILE="$RUNTIME_DIR/pids.txt"
VENV_DIR="$ROOT/.venv"
WEB_DIR="$ROOT/platform/web"
TAIL_PID_FILE="$RUNTIME_DIR/tail-pids.txt"
BOOTSTRAP_LAN_IP=""

mkdir -p "$LOG_DIR"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

detect_lan_ip() {
  local interface
  interface="$(route get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
  if [[ -n "$interface" ]]; then
    ipconfig getifaddr "$interface" 2>/dev/null || true
    return
  fi
  ipconfig getifaddr en0 2>/dev/null || true
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
  {
    echo ""
    echo "===== $(date -u +"%Y-%m-%dT%H:%M:%SZ") $name session start ====="
  } >>"$log_file"
  nohup bash -lc "$*" >>"$log_file" 2>&1 </dev/null &
  local pid=$!
  echo "$name:$pid" >>"$PID_FILE"
}

stream_log() {
  local label="$1"
  local color="$2"
  local file="$3"
  local include="${4:-}"
  local exclude="${5:-}"
  touch "$file"
  tail -n 25 -F "$file" 2>/dev/null | awk \
    -v prefix="[$label]" \
    -v color="$color" \
    -v reset='\033[0m' \
    -v include="$include" \
    -v exclude="$exclude" '
    {
      if (exclude != "" && $0 ~ exclude) {
        next;
      }
      if (include != "" && $0 !~ include) {
        next;
      }
      printf "%s%-18s%s %s\n", color, prefix, reset, $0;
      fflush(stdout);
    }
  ' &
  echo "$!" >>"$TAIL_PID_FILE"
}

cleanup_log_stream() {
  if [[ -f "$TAIL_PID_FILE" ]]; then
    while IFS= read -r pid; do
      [[ -n "$pid" ]] || continue
      kill "$pid" >/dev/null 2>&1 || true
      kill -9 "$pid" >/dev/null 2>&1 || true
    done <"$TAIL_PID_FILE"
    rm -f "$TAIL_PID_FILE"
  fi
}

stop_demo() {
  echo ""
  cleanup_log_stream
  "$DEMO_DIR/stop_demo.sh" >/dev/null 2>&1 || true
  echo "Demo stopped."
  exit 0
}

write_node_config() {
  local path="$1"
  local private_key="$2"
  local api_port="$3"
  local listen="$4"
  local peers="$5"
  local router_addr="${6:-}"
  local router_port="${7:-9003}"
  local a2a_addr="${8:-}"
  local a2a_port="${9:-9004}"

  cat >"$path" <<EOF
{
  "PrivateKeyPath": "$private_key",
  "Peers": $peers,
  "Listen": $listen,
  "api_port": $api_port,
  "bridge_addr": "127.0.0.1",
  "router_addr": "$router_addr",
  "router_port": $router_port,
  "a2a_addr": "$a2a_addr",
  "a2a_port": $a2a_port
}
EOF
}

cleanup_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill -9 >/dev/null 2>&1 || true
  fi
}

ensure_evm_key() {
  local path="$1"
  if [[ -f "$path" ]]; then
    return
  fi
  python - <<PY >"$path"
from eth_account import Account
print(Account.create().key.hex())
PY
}

require_cmd curl
require_cmd lsof
require_cmd openssl
require_cmd python3
require_cmd npm

BOOTSTRAP_LAN_IP="$(detect_lan_ip)"

if [[ ! -x "$ROOT/node" ]]; then
  echo "Missing ./node binary. Build it first with: make build" >&2
  exit 1
fi

if [[ -f "$PID_FILE" ]]; then
  "$DEMO_DIR/stop_demo.sh" >/dev/null 2>&1 || true
fi

for port in 3000 8010 8110 8210 9002 9005 9006 9007 9015 9016 9017 9101; do
  cleanup_port "$port"
done

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python -m pip install -e "$ROOT/platform[test]" -e "$ROOT/integrations[test]" >/dev/null

if [[ ! -d "$WEB_DIR/node_modules" ]]; then
  (cd "$WEB_DIR" && npm install >/dev/null)
fi

mkdir -p "$RUNTIME_DIR"
: >"$PID_FILE"
rm -f "$TAIL_PID_FILE"
rm -f "$RUNTIME_DIR"/worker-mumbai-node.json "$RUNTIME_DIR"/worker-mumbai.pem "$RUNTIME_DIR"/mumbai-wallet.key

WORKER1_KEY="$RUNTIME_DIR/worker-berlin.pem"
WORKER2_KEY="$RUNTIME_DIR/worker-tokyo.pem"
CUSTOMER_KEY="$RUNTIME_DIR/customer.pem"
[[ -f "$WORKER1_KEY" ]] || openssl genpkey -algorithm ed25519 -out "$WORKER1_KEY" >/dev/null 2>&1
[[ -f "$WORKER2_KEY" ]] || openssl genpkey -algorithm ed25519 -out "$WORKER2_KEY" >/dev/null 2>&1
[[ -f "$CUSTOMER_KEY" ]] || openssl genpkey -algorithm ed25519 -out "$CUSTOMER_KEY" >/dev/null 2>&1

CUSTOMER_WALLET_KEY="$RUNTIME_DIR/customer-wallet.key"
BERLIN_WALLET_KEY="$RUNTIME_DIR/berlin-wallet.key"
TOKYO_WALLET_KEY="$RUNTIME_DIR/tokyo-wallet.key"
ensure_evm_key "$CUSTOMER_WALLET_KEY"
ensure_evm_key "$BERLIN_WALLET_KEY"
ensure_evm_key "$TOKYO_WALLET_KEY"

write_node_config "$RUNTIME_DIR/worker-berlin-node.json" "$WORKER1_KEY" 9005 '["tls://0.0.0.0:9101"]' '[]' "http://127.0.0.1" 9006 "http://127.0.0.1" 8110
write_node_config "$RUNTIME_DIR/worker-tokyo-node.json" "$WORKER2_KEY" 9015 '[]' '["tls://127.0.0.1:9101"]' "http://127.0.0.1" 9016 "http://127.0.0.1" 8210
write_node_config "$RUNTIME_DIR/customer-node.json" "$CUSTOMER_KEY" 9002 '[]' '["tls://127.0.0.1:9101"]' "" 9003 "http://127.0.0.1" 8010

start_process "worker-berlin-node" "cd '$ROOT' && ./node -config '$RUNTIME_DIR/worker-berlin-node.json'"
sleep 2
start_process "worker-tokyo-node" "cd '$ROOT' && ./node -config '$RUNTIME_DIR/worker-tokyo-node.json'"
start_process "customer-node" "cd '$ROOT' && ./node -config '$RUNTIME_DIR/customer-node.json'"

start_process "worker-berlin-router" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && PYTHONPATH=integrations python -m mcp_routing.mcp_router --port 9006"
start_process "worker-tokyo-router" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && PYTHONPATH=integrations python -m mcp_routing.mcp_router --port 9016"

start_process "customer-daemon" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && PYTHONPATH=platform NODEHUB_DAEMON_HOST=127.0.0.1 NODEHUB_DAEMON_PORT=8010 NODEHUB_DAEMON_STATE_DIR='$RUNTIME_DIR/customer-state' NODEHUB_DAEMON_ENABLE_WORKER=false NODEHUB_AXL_NODE_URL=http://127.0.0.1:9002 NODEHUB_WORKER_PUBLIC_LABEL='Customer Daemon' NODEHUB_WORKER_REGION='control' NODEHUB_WORKER_COUNTRY_CODE='IN' NODEHUB_WALLET_PRIVATE_KEY_PATH='$CUSTOMER_WALLET_KEY' uvicorn daemon.app:app --host 127.0.0.1 --port 8010"
start_process "worker-berlin-daemon" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && PYTHONPATH=platform NODEHUB_DAEMON_HOST=127.0.0.1 NODEHUB_DAEMON_PORT=8110 NODEHUB_DAEMON_STATE_DIR='$RUNTIME_DIR/berlin-state' NODEHUB_DAEMON_ENABLE_WORKER=true NODEHUB_AXL_NODE_URL=http://127.0.0.1:9005 NODEHUB_ROUTER_URL=http://127.0.0.1:9006 NODEHUB_WORKER_PUBLIC_LABEL='Berlin Worker' NODEHUB_WORKER_REGION='berlin' NODEHUB_WORKER_COUNTRY_CODE='DE' NODEHUB_WALLET_PRIVATE_KEY_PATH='$BERLIN_WALLET_KEY' uvicorn daemon.app:app --host 127.0.0.1 --port 8110"
start_process "worker-tokyo-daemon" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && PYTHONPATH=platform NODEHUB_DAEMON_HOST=127.0.0.1 NODEHUB_DAEMON_PORT=8210 NODEHUB_DAEMON_STATE_DIR='$RUNTIME_DIR/tokyo-state' NODEHUB_DAEMON_ENABLE_WORKER=true NODEHUB_AXL_NODE_URL=http://127.0.0.1:9015 NODEHUB_ROUTER_URL=http://127.0.0.1:9016 NODEHUB_WORKER_PUBLIC_LABEL='Tokyo Worker' NODEHUB_WORKER_REGION='tokyo' NODEHUB_WORKER_COUNTRY_CODE='JP' NODEHUB_WALLET_PRIVATE_KEY_PATH='$TOKYO_WALLET_KEY' uvicorn daemon.app:app --host 127.0.0.1 --port 8210"

wait_for_http "http://127.0.0.1:8010/health"
wait_for_http "http://127.0.0.1:8110/health"
wait_for_http "http://127.0.0.1:8210/health"
wait_for_http "http://127.0.0.1:8110/.well-known/agent-card.json"
wait_for_http "http://127.0.0.1:8210/.well-known/agent-card.json"

start_process "dashboard" "cd '$WEB_DIR' && NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8010 NEXT_PUBLIC_OPERATOR_BOOTSTRAP_PEER='tls://${BOOTSTRAP_LAN_IP:-YOUR_BOOTSTRAP_HOST}:9101' npm run dev -- --hostname 127.0.0.1 --port 3000"
wait_for_http "http://127.0.0.1:3000"

echo ""
echo "NodeHub v2 demo is running."
echo "Dashboard: http://127.0.0.1:3000"
echo "Customer daemon: http://127.0.0.1:8010"
echo "Logs: $LOG_DIR"
if [[ -n "$BOOTSTRAP_LAN_IP" ]]; then
  echo "Remote operator seed peer: tls://$BOOTSTRAP_LAN_IP:9101"
else
  echo "Remote operator seed peer: unavailable (could not detect LAN IP)"
fi
echo ""
echo "Streaming live logs. Press Ctrl+C to stop the full demo."
echo ""
trap stop_demo INT TERM

: >"$TAIL_PID_FILE"
stream_log "WEB" $'\033[38;5;45m' "$LOG_DIR/dashboard.log" 'Starting|Ready|Compiled|Local:|Network:|WARN|Warning|ERROR|Error|Failed|Traceback'
stream_log "CUSTOMER-DAEMON" $'\033[38;5;220m' "$LOG_DIR/customer-daemon.log" 'Started server process|Application startup complete|Uvicorn running|POST /|ERROR|Traceback|Exception'
stream_log "CUSTOMER-NODE" $'\033[38;5;223m' "$LOG_DIR/customer-node.log"
stream_log "BERLIN-DAEMON" $'\033[38;5;42m' "$LOG_DIR/worker-berlin-daemon.log" 'Started server process|Application startup complete|Uvicorn running|POST /|ERROR|Traceback|Exception'
stream_log "BERLIN-NODE" $'\033[38;5;77m' "$LOG_DIR/worker-berlin-node.log"
stream_log "BERLIN-ROUTER" $'\033[38;5;84m' "$LOG_DIR/worker-berlin-router.log" 'listening|Endpoints:|Registered service|POST /route|POST /register|ERROR|Traceback|Exception'
stream_log "TOKYO-DAEMON" $'\033[38;5;208m' "$LOG_DIR/worker-tokyo-daemon.log" 'Started server process|Application startup complete|Uvicorn running|POST /|ERROR|Traceback|Exception'
stream_log "TOKYO-NODE" $'\033[38;5;214m' "$LOG_DIR/worker-tokyo-node.log"
stream_log "TOKYO-ROUTER" $'\033[38;5;215m' "$LOG_DIR/worker-tokyo-router.log" 'listening|Endpoints:|Registered service|POST /route|POST /register|ERROR|Traceback|Exception'

wait
