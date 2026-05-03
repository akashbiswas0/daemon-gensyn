#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT/platform/operator/runtime"
LOG_DIR="$RUNTIME_DIR/logs"
PID_FILE="$RUNTIME_DIR/pids.txt"
VENV_DIR="$ROOT/.venv"
WORKER_ENV_FILE="$RUNTIME_DIR/worker.env"
STATE_DIR="$RUNTIME_DIR/worker-state"
BROWSER_RUNTIME_DIR="$RUNTIME_DIR/browser-runtime"
BROWSER_ARTIFACT_DIR="$BROWSER_RUNTIME_DIR/artifacts"
NODE_CONFIG_PATH="$RUNTIME_DIR/worker-node.json"
NODE_KEY_PATH="$RUNTIME_DIR/worker-node.pem"
SIGNING_KEY_PATH="$RUNTIME_DIR/worker-signing-wallet.key"
NEXUS_DIR="$ROOT/node-nexus-agent"
NEXUS_VENV_DIR="$BROWSER_RUNTIME_DIR/python-agent/venv"
NEXUS_PORT="8080"
NEXUS_URL="http://127.0.0.1:${NEXUS_PORT}"
LOG_PREFIX="operator-worker"

LABEL=""
REGION=""
COUNTRY=""
PAYOUT_WALLET=""
CAPABILITIES=""
SEED_PEER=""
OPENAI_ENABLED="false"
OPENAI_KEY="${NODEHUB_OPENAI_API_KEY:-}"
ZEROG_API_KEY="${ZEROG_API_KEY:-}"
ZEROG_PRIVATE_KEY="${ZEROG_PRIVATE_KEY:-}"
PYTHON_BIN=""

usage() {
  cat <<'EOF'
Usage:
  ./OnboardWorker \
    --label "London Worker" \
    --region london \
    --country GB \
    --payout-wallet 0x... \
    --capabilities browser_task,http_check \
    --seed-peer tls://bootstrap.example.com:9101 \
    [--openai-enabled true|false]

Capabilities:
  browser_task   Primary capability. Runs 0G-backed browser workflows.
  http_check     Optional secondary capability for lightweight HTTP probes.
EOF
}

log_step() {
  printf "\n[%s] %s\n" "OnboardWorker" "$1"
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
    log_step "Go already installed."
    return
  fi

  refresh_homebrew_path
  if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    log_step "Go not found. Installing Go with Homebrew..."
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

python_version_at_least_312() {
  local candidate="$1"
  "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

ensure_python312() {
  refresh_homebrew_path

  local candidates=()
  if command -v python3.12 >/dev/null 2>&1; then
    candidates+=("$(command -v python3.12)")
  fi
  if command -v python3 >/dev/null 2>&1; then
    candidates+=("$(command -v python3)")
  fi

  local candidate
  for candidate in "${candidates[@]}"; do
    if python_version_at_least_312 "$candidate"; then
      PYTHON_BIN="$candidate"
      log_step "Using Python at $PYTHON_BIN."
      return
    fi
  done

  if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    log_step "Python 3.12+ not found. Installing python@3.12 with Homebrew..."
    brew install python@3.12
    refresh_homebrew_path
    if command -v python3.12 >/dev/null 2>&1 && python_version_at_least_312 "$(command -v python3.12)"; then
      PYTHON_BIN="$(command -v python3.12)"
      log_step "Using Python at $PYTHON_BIN."
      return
    fi
  fi

  echo "Python 3.12 or newer is required for this project." >&2
  echo "Install python@3.12, then rerun onboarding." >&2
  exit 1
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

  if [[ "$ENABLE_NEXUS_AGENT" == "true" ]]; then
    files+=("$LOG_DIR/${LOG_PREFIX}-nexus.log")
  fi

  echo ""
  echo "Streaming worker logs. Press Ctrl+C to stop following logs; the worker keeps running."
  exec tail -n 20 -F "${files[@]}"
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

listening_process_info() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

primary_lan_ip() {
  # Best-effort: pick the IPv4 address of the default-route interface. Falls
  # back to the first non-loopback IPv4 the host advertises.
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
  # Quick sanity check that the host accepts TCP on the LAN-facing IP. If
  # the App Firewall is dropping packets we want to flag it before the
  # operator wonders why the other laptop can't reach them.
  local lan_ip="$1"
  if [[ -z "$lan_ip" ]]; then
    return 0
  fi
  if ! command -v nc >/dev/null 2>&1; then
    return 0
  fi
  if nc -G 2 -z "$lan_ip" 9101 >/dev/null 2>&1; then
    echo "[Onboard] Self-probe: $lan_ip:9101 is reachable."
    return 0
  fi
  echo "" >&2
  echo "[Onboard] WARNING: $lan_ip:9101 is not reachable from this host." >&2
  echo "  This usually means the macOS Application Firewall is blocking the node binary." >&2
  echo "  Run:  sudo $ROOT/platform/operator/configure_firewall.sh" >&2
  echo "  Then retest with:  nc -vz $lan_ip 9101" >&2
  return 1
}

try_configure_firewall() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi
  if [[ ! -x "$ROOT/platform/operator/configure_firewall.sh" ]]; then
    return 0
  fi
  # Only attempt if the operator already has cached sudo credentials; never
  # prompt during onboarding. Fall back to printed instructions otherwise.
  if sudo -n true >/dev/null 2>&1; then
    log_step "Configuring macOS Application Firewall for the node binary."
    sudo -n "$ROOT/platform/operator/configure_firewall.sh" || true
  else
    echo ""
    echo "[Onboard] To allow inbound peer connections on port 9101, run once:"
    echo "  sudo $ROOT/platform/operator/configure_firewall.sh"
  fi
}

port_in_use() {
  local port="$1"
  [[ -n "$(listening_process_info "$port")" ]]
}

ensure_port_free() {
  local port="$1"
  if port_in_use "$port"; then
    echo "Port $port is already in use by:" >&2
    listening_process_info "$port" >&2
    echo "Stop the conflicting process before onboarding a worker." >&2
    exit 1
  fi
}

ensure_evm_key() {
  local path="$1"
  if [[ -f "$path" ]]; then
    return
  fi
  local keygen_python="$PYTHON_BIN"
  if [[ -n "${VIRTUAL_ENV:-}" ]] && command -v python >/dev/null 2>&1; then
    keygen_python="$(command -v python)"
  elif [[ -x "$VENV_DIR/bin/python" ]]; then
    keygen_python="$VENV_DIR/bin/python"
  fi
  "$keygen_python" - <<PY >"$path"
from eth_account import Account
print(Account.create().key.hex())
PY
}

verify_runtime_python_deps() {
  if ! python - <<'PY' >/dev/null 2>&1
from eth_account import Account
import fastapi
import httpx
import pydantic_settings
print(Account)
PY
  then
    echo "Worker runtime Python dependencies did not import correctly from the virtual environment." >&2
    echo "Try removing .venv and rerunning onboarding." >&2
    exit 1
  fi
}

prompt_secret_if_missing() {
  local var_name="$1"
  local prompt="$2"
  local current_value="${!var_name:-}"
  if [[ -n "$current_value" ]]; then
    return
  fi
  read -r -s -p "$prompt" current_value
  echo ""
  if [[ -z "$current_value" ]]; then
    echo "$var_name is required when browser_task is enabled." >&2
    exit 1
  fi
  printf -v "$var_name" '%s' "$current_value"
}

setup_browser_runtime() {
  require_cmd node
  require_cmd npm

  if [[ ! -d "$NEXUS_DIR" ]]; then
    echo "Browser runtime sources not found at $NEXUS_DIR" >&2
    exit 1
  fi

  log_step "Preparing NodeHub browser runtime dependencies."
  mkdir -p "$BROWSER_RUNTIME_DIR" "$BROWSER_ARTIFACT_DIR"

  if [[ ! -d "$NEXUS_DIR/node_modules" ]]; then
    (cd "$NEXUS_DIR" && npm install --no-fund --no-audit)
  else
    log_step "Reusing existing browser runtime node_modules."
  fi

  if [[ ! -d "$NEXUS_VENV_DIR" ]]; then
    log_step "Creating browser runtime Python virtual environment."
    mkdir -p "$(dirname "$NEXUS_VENV_DIR")"
    "$PYTHON_BIN" -m venv "$NEXUS_VENV_DIR"
  fi

  local nexus_python
  nexus_python="$NEXUS_VENV_DIR/bin/python3"

  log_step "Installing browser runtime Python dependencies."
  "$nexus_python" -m pip install --disable-pip-version-check -r "$NEXUS_DIR/python-agent/requirements.txt"

  if [[ ! -f "$NEXUS_VENV_DIR/.playwright-ready" ]]; then
    log_step "Installing Playwright Chromium for browser runtime."
    PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$BROWSER_RUNTIME_DIR/playwright-browsers}" \
      "$nexus_python" -m playwright install chromium
    touch "$NEXUS_VENV_DIR/.playwright-ready"
  else
    log_step "Reusing existing Playwright Chromium install for browser runtime."
  fi
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
    --zerog-api-key)
      ZEROG_API_KEY="${2:-}"
      shift 2
      ;;
    --zerog-private-key)
      ZEROG_PRIVATE_KEY="${2:-}"
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
VALID_CAPABILITIES=(browser_task http_check)
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

ENABLE_NEXUS_AGENT="false"
for capability in "${CANONICAL_CAPABILITIES[@]}"; do
  if [[ "$capability" == "browser_task" ]]; then
    ENABLE_NEXUS_AGENT="true"
    break
  fi
done

if [[ ${#CANONICAL_CAPABILITIES[@]} -eq 0 ]]; then
  echo "At least one worker capability must be enabled." >&2
  exit 1
fi

if [[ "$ENABLE_NEXUS_AGENT" != "true" ]]; then
  echo "browser_task is the primary NodeHub capability and must be enabled." >&2
  exit 1
fi

require_cmd curl
require_cmd lsof
require_cmd openssl
require_cmd make
ensure_go
ensure_python312

if [[ -f "$WORKER_ENV_FILE" ]]; then
  # Reuse previously stored credentials if the operator already onboarded this worker.
  # shellcheck disable=SC1090
  source "$WORKER_ENV_FILE"
  if [[ "$OPENAI_ENABLED" == "true" && -z "$OPENAI_KEY" ]]; then
    OPENAI_KEY="${NODEHUB_OPENAI_API_KEY:-}"
  fi
fi

if [[ "$OPENAI_ENABLED" == "true" && -z "$OPENAI_KEY" ]]; then
  read -r -s -p "OpenAI API key (used only on this machine): " OPENAI_KEY
  echo ""
fi

if [[ "$OPENAI_ENABLED" != "true" ]]; then
  OPENAI_KEY=""
fi

if [[ "$ENABLE_NEXUS_AGENT" == "true" ]]; then
  prompt_secret_if_missing "ZEROG_API_KEY" "0G router API key (stored in the local worker runtime): "
  prompt_secret_if_missing "ZEROG_PRIVATE_KEY" "0G storage private key (stored in the local worker runtime): "
fi

log_step "Stopping any previous local operator worker processes."
"$ROOT/platform/operator/stop_worker.sh" >/dev/null 2>&1 || true

for port in 9005 9006 8110 9101; do
  ensure_port_free "$port"
done

if [[ "$ENABLE_NEXUS_AGENT" == "true" ]]; then
  ensure_port_free "$NEXUS_PORT"
fi

if [[ ! -x "$ROOT/node" ]]; then
  log_step "Building the AXL node binary."
  (cd "$ROOT" && make build)
else
  log_step "Reusing existing AXL node binary."
fi

if [[ ! -d "$VENV_DIR" ]]; then
  log_step "Creating Python virtual environment."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

log_step "Installing Python dependencies for the worker runtime."
source "$VENV_DIR/bin/activate"
python -m pip install --disable-pip-version-check -e "$ROOT/platform" -e "$ROOT/integrations"
verify_runtime_python_deps

if [[ "$ENABLE_NEXUS_AGENT" == "true" ]]; then
  setup_browser_runtime
fi

mkdir -p "$RUNTIME_DIR" "$LOG_DIR" "$STATE_DIR" "$BROWSER_RUNTIME_DIR" "$BROWSER_ARTIFACT_DIR"
: >"$PID_FILE"
[[ -f "$NODE_KEY_PATH" ]] || openssl genpkey -algorithm ed25519 -out "$NODE_KEY_PATH" >/dev/null 2>&1
ensure_evm_key "$SIGNING_KEY_PATH"
log_step "Writing local worker runtime config."
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
write_env_line "NODEHUB_NODE_NEXUS_AGENT_ENABLED" "$ENABLE_NEXUS_AGENT"
write_env_line "NODEHUB_NODE_NEXUS_AGENT_URL" "$NEXUS_URL"
write_env_line "NODEHUB_AGENTIC_ENABLED" "true"
write_env_line "NODEHUB_OPENAI_API_KEY" "$OPENAI_KEY"

if [[ "$ENABLE_NEXUS_AGENT" == "true" ]]; then
  # Browser runtime env. The vendored browser runtime reads these values from
  # the unified worker runtime env; operators do not manage a separate app or
  # a separate `.env`.
  write_env_line "NODE_NEXUS_RUNTIME_DIR" "$BROWSER_RUNTIME_DIR"
  write_env_line "NODE_NEXUS_ARTIFACT_ROOT" "$BROWSER_ARTIFACT_DIR"
  write_env_line "NODE_NEXUS_ENV_FILE" "$WORKER_ENV_FILE"
  write_env_line "NODE_NEXUS_PYTHON_BIN" "$NEXUS_VENV_DIR/bin/python3"
  write_env_line "NODE_NAME" "${NODE_NAME:-pookie-laptop-node1}"
  write_env_line "ENS_IDENTITY" "${ENS_IDENTITY:-your-node.eth}"
  write_env_line "ZEROG_API_KEY" "$ZEROG_API_KEY"
  write_env_line "ZEROG_PRIVATE_KEY" "$ZEROG_PRIVATE_KEY"
  write_env_line "ZEROG_BASE_URL" "${ZEROG_BASE_URL:-https://router-api-testnet.integratenetwork.work/v1}"
  write_env_line "ZEROG_MODEL" "${ZEROG_MODEL:-qwen/qwen-2.5-7b-instruct}"
  write_env_line "ZEROG_STORAGE_RPC_URL" "${ZEROG_STORAGE_RPC_URL:-https://evmrpc-testnet.0g.ai}"
  write_env_line "ZEROG_STORAGE_INDEXER_RPC" "${ZEROG_STORAGE_INDEXER_RPC:-https://indexer-storage-testnet-turbo.0g.ai}"
  write_env_line "BROWSER_HEADLESS" "${BROWSER_HEADLESS:-false}"
  write_env_line "ARTIFACT_RETENTION" "${ARTIFACT_RETENTION:-keep}"
  write_env_line "PLAYWRIGHT_BROWSERS_PATH" "${PLAYWRIGHT_BROWSERS_PATH:-$BROWSER_RUNTIME_DIR/playwright-browsers}"
fi

start_process "${LOG_PREFIX}-node" "cd '$ROOT' && ./node -config '$NODE_CONFIG_PATH'"
log_step "Starting local AXL node."
wait_for_http "http://127.0.0.1:9005/topology"
start_process "${LOG_PREFIX}-router" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && PYTHONPATH=integrations python -m mcp_routing.mcp_router --port 9006"
log_step "Starting MCP router."
wait_for_http "http://127.0.0.1:9006/health"

if [[ "$ENABLE_NEXUS_AGENT" == "true" ]]; then
  start_process "${LOG_PREFIX}-nexus" "cd '$NEXUS_DIR' && set -a && source '$WORKER_ENV_FILE' && set +a && node src/server.js"
  log_step "Starting NodeHub browser runtime."
  if ! wait_for_http "${NEXUS_URL}/health"; then
    print_log_tail "Browser runtime log" "$LOG_DIR/${LOG_PREFIX}-nexus.log"
    exit 1
  fi
fi

start_process "${LOG_PREFIX}-daemon" "cd '$ROOT' && source '$VENV_DIR/bin/activate' && set -a && source '$WORKER_ENV_FILE' && set +a && PYTHONPATH=platform uvicorn daemon.app:app --host 127.0.0.1 --port 8110"
log_step "Starting worker daemon."

log_step "Waiting for worker daemon health."
if ! wait_for_http "http://127.0.0.1:8110/health"; then
  print_log_tail "Node log" "$LOG_DIR/${LOG_PREFIX}-node.log"
  print_log_tail "Router log" "$LOG_DIR/${LOG_PREFIX}-router.log"
  print_log_tail "Node Nexus log" "$LOG_DIR/${LOG_PREFIX}-nexus.log"
  print_log_tail "Daemon log" "$LOG_DIR/${LOG_PREFIX}-daemon.log"
  exit 1
fi

log_step "Waiting for A2A agent card."
if ! wait_for_http "http://127.0.0.1:8110/.well-known/agent-card.json"; then
  print_log_tail "Daemon log" "$LOG_DIR/${LOG_PREFIX}-daemon.log"
  exit 1
fi

IDENTITY_JSON="$(curl -fsS http://127.0.0.1:8110/identity)"
TOPOLOGY_JSON="$(curl -fsS http://127.0.0.1:9005/topology)"
PEER_ID="$(python - <<PY
import json
print(json.loads("""$TOPOLOGY_JSON""")["our_public_key"])
PY
)"

try_configure_firewall

LAN_IP="$(primary_lan_ip)"
probe_inbound_9101 "$LAN_IP" || true

echo ""
echo "NodeHub worker is live."
echo "Peer ID: $PEER_ID"
echo "Payout wallet: $LOWER_PAYOUT_WALLET"
echo "Daemon: http://127.0.0.1:8110"
echo "AXL API: http://127.0.0.1:9005"
if [[ "$ENABLE_NEXUS_AGENT" == "true" ]]; then
  echo "Browser runtime: ${NEXUS_URL}"
fi
if [[ -n "$LAN_IP" ]]; then
  echo "Peer URI to share: tls://$LAN_IP:9101"
fi
echo "Logs: $LOG_DIR"
echo ""
echo "What success looks like:"
echo "- Your worker appears in the requester dashboard /nodes page after discovery."
echo "- Jobs routed to this peer return signed receipts under peer ID $PEER_ID."
echo "- Requester-side 0G payouts target $LOWER_PAYOUT_WALLET on 0G Galileo testnet."
echo ""
echo "To stop this worker later:"
echo "  $ROOT/platform/operator/stop_worker.sh"
stream_runtime_logs
