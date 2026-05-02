#!/usr/bin/env bash
set -euo pipefail

# Adds the AXL node binary to the macOS Application Firewall whitelist so that
# peers on the same network can reach the TLS listener on port 9101. Without
# this, macOS silently drops inbound TCP SYNs to unsigned binaries and remote
# `nc -vz <lan-ip> 9101` calls hang with "Operation timed out".
#
# Usage:
#   sudo ./platform/operator/configure_firewall.sh

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE_BIN="$ROOT/node"
FW=/usr/libexec/ApplicationFirewall/socketfilterfw

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "configure_firewall.sh is only needed on macOS." >&2
  exit 0
fi

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This script must be run with sudo (it edits the system Application Firewall)." >&2
  echo "Re-run as: sudo $0" >&2
  exit 1
fi

if [[ ! -x "$NODE_BIN" ]]; then
  echo "Missing executable node binary at $NODE_BIN. Build it first with: make build" >&2
  exit 1
fi

if [[ ! -x "$FW" ]]; then
  echo "socketfilterfw not found at $FW; cannot configure the App Firewall." >&2
  exit 1
fi

echo "[firewall] Adding $NODE_BIN to the Application Firewall whitelist."
"$FW" --add "$NODE_BIN" >/dev/null
"$FW" --unblockapp "$NODE_BIN" >/dev/null

# Stealth mode silently drops inbound packets even for whitelisted apps when
# the connection isn't preceded by an outbound request. That breaks `nc -vz`
# probes and fresh inbound peerings, so disable it for this worker.
if "$FW" --getstealthmode | grep -qi "enabled"; then
  echo "[firewall] Disabling stealth mode (was on)."
  "$FW" --setstealthmode off >/dev/null
fi

# Make sure the firewall itself is on; if it's off, we don't need to touch it.
state="$("$FW" --getglobalstate || true)"
echo "[firewall] State: $state"
echo "[firewall] Done. Inbound TCP to port 9101 should now be allowed."
