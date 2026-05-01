#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT/platform/operator/runtime"
LOG_DIR="$RUNTIME_DIR/logs"
PID_FILE="$RUNTIME_DIR/pids.txt"
VENV_DIR="$ROOT/.venv"
WORKER_ENV_FILE="$RUNTIME_DIR/worker.env"
STATE_DIR="$RUNTIME_DIR/worker-state"
NODE_CONFIG_PATH="$RUNTIME_DIR/worker-node.json"
NODE_KEY_PATH="$RUNTIME_DIR/worker-node.pem"
SIGNING_KEY_PATH="$RUNTIME_DIR/worker-signing-wallet.key"
LOG_PREFIX="operator-worker"

LABEL=""
REGION=""
COUNTRY=""
PAYOUT_WALLET=""
CAPABILITIES=""
SEED_PEER=""
OPENAI_ENABLED="false"
OPENAI_KEY="${NODEHUB_OPENAI_API_KEY:-}"

usage() {
  cat <<'EOF'
Usage:
  ./OnboardWorker \
    --label "London Worker" \
    --region london \
    --country GB \
    --payout-wallet 0x... \
    --capabilities http_check,dns_check,ping_check \
    --seed-peer tls://bootstrap.example.com:9101 \
    [--openai-enabled true|false]
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

refresh_homebrew_path() {
  PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
}

ensure_go() {
  if command -v go >/dev/null 2>&1; then
    return
  fi

  refresh_homebrew_path
  if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    echo "Go not found. Installing Go with Homebrew..."
    brew install go
    refresh_homebrew_path
  else
    echo "Missing required command: go" >&2
    echo "Install Go first, or install Homebrew so this script can install it automatically on macOS." >&2
    exit 1
  fi

  command -v go >/dev/null 2>&1 || {
    echo "Go installation finished, but 'go' is still not available on PATH." >&2
    exit 1
  }
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

port_in_use() {
  local port="$1"
  lsof -ti tcp:"$port" >/dev/null 2>&1
}

ensure_port_free() {
  local port="$1"
  if port_in_use "$port"; then
    echo "Port $port is already in use. Stop the conflicting process before onboarding a worker." >&2
    exit 1
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

write_node_config() {
  cat >"$NODE_CONFIG_PATH" <<EOF
{
  "PrivateKeyPath": "$NODE_KEY_PATH",
  "Peers": ["$SEED_PEER"],
  "Listen": ["tls://0.0.0.0:9101"],
  "api_port": 9005,
  "bridge_addr": "127.0.0.1",
  "router_addr": "http://127.0.0.1",
  "router_port": 9006,
  "a2a_addr": "http://127.0.0.1",
  "a2a_port": 8110
}
EOF
}

write_env_line() {
  local key="$1"
  local value="$2"
  printf "%s=%q\n" "$key" "$value" >>"$WORKER_ENV_FILE"
}

normalize_bool() {
  local lowered
  lowered="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "$lowered" in
    true|1|yes|y) echo "true" ;;
    false|0|no|n|"") echo "false" ;;
    *)
      echo "Expected boolean value for --openai-enabled, got: $1" >&2
      exit 1
      ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --label)
      LABEL="${2:-}"
      shift 2
      ;;
    --region)
      REGION="${2:-}"
      shift 2
      ;;
    --country)
      COUNTRY="${2:-}"
      shift 2
      ;;
    --payout-wallet)
      PAYOUT_WALLET="${2:-}"
      shift 2
      ;;
    --capabilities)
      CAPABILITIES="${2:-}"
      shift 2
      ;;
    --seed-peer)
      SEED_PEER="${2:-}"
      shift 2
      ;;
    --openai-enabled)
      OPENAI_ENABLED="$(normalize_bool "${2:-}")"
      shift 2
      ;;
    --openai-api-key)
      OPENAI_KEY="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

LABEL="${LABEL:-Operator Worker}"
REGION="$(printf '%s' "$REGION" | tr '[:upper:]' '[:lower:]')"
COUNTRY="$(printf '%s' "$COUNTRY" | tr '[:lower:]' '[:upper:]')"

if [[ -z "$REGION" || -z "$COUNTRY" || -z "$PAYOUT_WALLET" || -z "$CAPABILITIES" || -z "$SEED_PEER" ]]; then
  usage
  exit 1
fi

if [[ ! "$PAYOUT_WALLET" =~ ^0x[a-fA-F0-9]{40}$ ]]; then
  echo "Invalid payout wallet address: $PAYOUT_WALLET" >&2
  exit 1
fi

if [[ "$COUNTRY" =~ [^A-Z] || ${#COUNTRY} -ne 2 ]]; then
  echo "Country must be a two-letter uppercase code, got: $COUNTRY" >&2
  exit 1
fi

if [[ "$SEED_PEER" == *"YOUR_BOOTSTRAP_HOST"* ]]; then
  echo "Replace the placeholder bootstrap peer URI before running onboarding." >&2
  exit 1
fi

IFS=, read -r -a raw_capabilities <<<"$CAPABILITIES"
VALID_CAPABILITIES=(http_check dns_check latency_probe ping_check api_call cdn_check)
CANONICAL_CAPABILITIES=()
for item in "${raw_capabilities[@]}"; do
  trimmed="$(echo "$item" | xargs)"
  [[ -n "$trimmed" ]] || continue
  valid="false"
  for allowed in "${VALID_CAPABILITIES[@]}"; do
    if [[ "$trimmed" == "$allowed" ]]; then
      valid="true"
      CANONICAL_CAPABILITIES+=("$trimmed")
      break
    fi
  done
  if [[ "$valid" != "true" ]]; then
    echo "Unsupported capability: $trimmed" >&2
    exit 1
  fi
done

if [[ ${#CANONICAL_CAPABILITIES[@]} -eq 0 ]]; then
  echo "At least one worker capability must be enabled." >&2
  exit 1
fi

require_cmd curl
require_cmd lsof
require_cmd openssl
require_cmd python3
require_cmd make
ensure_go

if [[ "$OPENAI_ENABLED" == "true" && -z "$OPENAI_KEY" && -f "$WORKER_ENV_FILE" ]]; then
  # Reuse a previously stored key if the operator already onboarded this worker.
  # shellcheck disable=SC1090
  source "$WORKER_ENV_FILE"
  OPENAI_KEY="${NODEHUB_OPENAI_API_KEY:-}"
fi

if [[ "$OPENAI_ENABLED" == "true" && -z "$OPENAI_KEY" ]]; then
  read -r -s -p "OpenAI API key (used only on this machine): " OPENAI_KEY
  echo ""
fi

if [[ "$OPENAI_ENABLED" != "true" ]]; then
  OPENAI_KEY=""
fi

if [[ -f "$PID_FILE" ]]; then
  "$ROOT/platform/operator/stop_worker.sh" >/dev/null 2>&1 || true
fi

for port in 9005 9006 8110 9101; do
  ensure_port_free "$port"
done

if [[ ! -x "$ROOT/node" ]]; then
  (cd "$ROOT" && make build >/dev/null)
fi

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python -m pip install -e "$ROOT/platform[test]" -e "$ROOT/integrations[test]" >/dev/null

mkdir -p "$RUNTIME_DIR" "$LOG_DIR" "$STATE_DIR"
: >"$PID_FILE"
[[ -f "$NODE_KEY_PATH" ]] || openssl genpkey -algorithm ed25519 -out "$NODE_KEY_PATH" >/dev/null 2>&1
ensure_evm_key "$SIGNING_KEY_PATH"
write_node_config

: >"$WORKER_ENV_FILE"
write_env_line "NODEHUB_DAEMON_HOST" "127.0.0.1"
write_env_line "NODEHUB_DAEMON_PORT" "8110"
write_env_line "NODEHUB_DAEMON_STATE_DIR" "$STATE_DIR"
write_env_line "NODEHUB_DAEMON_ENABLE_WORKER" "true"
write_env_line "NODEHUB_AXL_NODE_URL" "http://127.0.0.1:9005"
write_env_line "NODEHUB_ROUTER_URL" "http://127.0.0.1:9006"
write_env_line "NODEHUB_WORKER_PUBLIC_LABEL" "$LABEL"
write_env_line "NODEHUB_WORKER_REGION" "$REGION"
write_env_line "NODEHUB_WORKER_COUNTRY_CODE" "$COUNTRY"
write_env_line "NODEHUB_WALLET_PRIVATE_KEY_PATH" "$SIGNING_KEY_PATH"
LOWER_PAYOUT_WALLET="$(printf '%s' "$PAYOUT_WALLET" | tr '[:upper:]' '[:lower:]')"

write_env_line "NODEHUB_WORKER_PAYOUT_WALLET" "$LOWER_PAYOUT_WALLET"
write_env_line "NODEHUB_WORKER_ENABLED_CAPABILITIES" "$(IFS=,; echo "${CANONICAL_CAPABILITIES[*]}")"
write_env_line "NODEHUB_AGENTIC_ENABLED" "true"
write_env_line "NODEHUB_OPENAI_API_KEY" "$OPENAI_KEY"

start_process "${LOG_PREFIX}-node" "cd '$ROOT' && ./node -config '$NODE_CONFIG_PATH'"
wait_for_http "http://127.0.0.1:9005/topology"
start_process "${LOG_PREFIX}-router" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && PYTHONPATH=integrations python -m mcp_routing.mcp_router --port 9006"
start_process "${LOG_PREFIX}-daemon" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && set -a && source '$WORKER_ENV_FILE' && set +a && PYTHONPATH=platform uvicorn daemon.app:app --host 127.0.0.1 --port 8110"

wait_for_http "http://127.0.0.1:8110/health"
wait_for_http "http://127.0.0.1:8110/.well-known/agent-card.json"

IDENTITY_JSON="$(curl -fsS http://127.0.0.1:8110/identity)"
TOPOLOGY_JSON="$(curl -fsS http://127.0.0.1:9005/topology)"
PEER_ID="$(python - <<PY
import json
print(json.loads("""$TOPOLOGY_JSON""")["our_public_key"])
PY
)"

echo ""
echo "NodeHub worker is live."
echo "Peer ID: $PEER_ID"
echo "Payout wallet: $LOWER_PAYOUT_WALLET"
echo "Daemon: http://127.0.0.1:8110"
echo "AXL API: http://127.0.0.1:9005"
echo "Logs: $LOG_DIR"
echo ""
echo "What success looks like:"
echo "- Your worker appears in the requester dashboard /nodes page after discovery."
echo "- Jobs routed to this peer return signed receipts under peer ID $PEER_ID."
echo "- Future requester-side KeeperHub payouts target $LOWER_PAYOUT_WALLET."
echo ""
echo "To stop this worker later:"
echo "  $ROOT/platform/operator/stop_worker.sh"
