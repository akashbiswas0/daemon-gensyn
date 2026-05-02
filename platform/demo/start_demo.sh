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

for port in 3000 8010 9002 9101; do
  cleanup_port "$port"
done

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python -m pip install --disable-pip-version-check -e "$ROOT/platform" -e "$ROOT/integrations" >/dev/null

if [[ ! -d "$WEB_DIR/node_modules" ]]; then
  (cd "$WEB_DIR" && npm install >/dev/null)
fi

mkdir -p "$RUNTIME_DIR"
: >"$PID_FILE"
rm -f "$TAIL_PID_FILE"

CUSTOMER_KEY="$RUNTIME_DIR/customer.pem"
[[ -f "$CUSTOMER_KEY" ]] || openssl genpkey -algorithm ed25519 -out "$CUSTOMER_KEY" >/dev/null 2>&1

CUSTOMER_WALLET_KEY="$RUNTIME_DIR/customer-wallet.key"
ensure_evm_key "$CUSTOMER_WALLET_KEY"

write_node_config \
  "$RUNTIME_DIR/customer-node.json" \
  "$CUSTOMER_KEY" \
  9002 \
  '["tls://0.0.0.0:9101"]' \
  '[]' \
  "" \
  9003 \
  "http://127.0.0.1" \
  8010

start_process "customer-node" "cd '$ROOT' && ./node -config '$RUNTIME_DIR/customer-node.json'"

CORS_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
if [[ -n "$BOOTSTRAP_LAN_IP" ]]; then
  CORS_ORIGINS="$CORS_ORIGINS,http://$BOOTSTRAP_LAN_IP:3000"
fi

start_process "customer-daemon" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && PYTHONPATH=platform NODEHUB_DAEMON_HOST=0.0.0.0 NODEHUB_DAEMON_PORT=8010 NODEHUB_DAEMON_STATE_DIR='$RUNTIME_DIR/customer-state' NODEHUB_DAEMON_ENABLE_WORKER=false NODEHUB_AXL_NODE_URL=http://127.0.0.1:9002 NODEHUB_WORKER_PUBLIC_LABEL='Customer Daemon' NODEHUB_WORKER_REGION='control' NODEHUB_WORKER_COUNTRY_CODE='IN' NODEHUB_WALLET_PRIVATE_KEY_PATH='$CUSTOMER_WALLET_KEY' NODEHUB_CORS_ALLOWED_ORIGINS='$CORS_ORIGINS' uvicorn daemon.app:app --host 0.0.0.0 --port 8010"

wait_for_http "http://127.0.0.1:8010/health"

DASHBOARD_API_BASE="http://127.0.0.1:8010"
DASHBOARD_BIND_HOST="127.0.0.1"
if [[ -n "$BOOTSTRAP_LAN_IP" ]]; then
  DASHBOARD_API_BASE="http://$BOOTSTRAP_LAN_IP:8010"
  DASHBOARD_BIND_HOST="0.0.0.0"
fi
start_process "dashboard" "cd '$WEB_DIR' && NEXT_PUBLIC_API_BASE_URL=$DASHBOARD_API_BASE NEXT_PUBLIC_OPERATOR_BOOTSTRAP_PEER='tls://${BOOTSTRAP_LAN_IP:-YOUR_BOOTSTRAP_HOST}:9101' npm run dev -- --hostname $DASHBOARD_BIND_HOST --port 3000"
wait_for_http "http://127.0.0.1:3000"

echo ""
echo "NodeHub two-laptop demo is running."
if [[ -n "$BOOTSTRAP_LAN_IP" ]]; then
  echo "Dashboard:        http://127.0.0.1:3000  (also reachable on LAN: http://$BOOTSTRAP_LAN_IP:3000)"
  echo "Customer daemon:  http://127.0.0.1:8010  (also reachable on LAN: http://$BOOTSTRAP_LAN_IP:8010)"
else
  echo "Dashboard: http://127.0.0.1:3000"
  echo "Customer daemon: http://127.0.0.1:8010"
fi
echo "Logs: $LOG_DIR"
if [[ -n "$BOOTSTRAP_LAN_IP" ]]; then
  echo "Remote operator seed peer: tls://$BOOTSTRAP_LAN_IP:9101"
else
  echo "Remote operator seed peer: unavailable (could not detect LAN IP)"
fi
echo ""
echo "Streaming live logs. Press Ctrl+C to stop the requester stack."
echo ""
trap stop_demo INT TERM

: >"$TAIL_PID_FILE"
stream_log "WEB" $'\033[38;5;45m' "$LOG_DIR/dashboard.log" 'Starting|Ready|Compiled|Local:|Network:|WARN|Warning|ERROR|Error|Failed|Traceback'
stream_log "CUSTOMER-DAEMON" $'\033[38;5;220m' "$LOG_DIR/customer-daemon.log" 'Started server process|Application startup complete|Uvicorn running|POST /|ERROR|Traceback|Exception|raw send|recv ok|recv:|recv_loop|discover:|announce:|store ad'
stream_log "CUSTOMER-NODE" $'\033[38;5;223m' "$LOG_DIR/customer-node.log"

wait
