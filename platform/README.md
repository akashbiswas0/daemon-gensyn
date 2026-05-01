# NodeHub

Local-first NodeHub daemons built on top of the existing AXL node.

## Services

- `daemon`: discovery, leasing, jobs, receipts, attestations, and worker execution
- `shared`: contracts, settings, and task plugins
- `web`: Next.js dashboard

## V2 Local Run Order

1. Start the existing AXL node on each host.
2. Start the local MCP router:
   `python -m mcp_routing.mcp_router --port 9003`
3. Start the local daemon:
   `PYTHONPATH=platform uvicorn daemon.app:app --reload --port 8010`
4. Start the dashboard:
   `cd platform/web && npm install && npm run dev`

AXL usage in the current runtime follows the repo conventions:

- the daemon exposes the A2A agent card and coordination methods directly
- MCP carries deterministic worker tool execution over AXL
- A2A carries NodeHub coordination over AXL
- the dashboard talks only to the local daemon

## Environment

Important variables:

- `NODEHUB_DAEMON_HOST`
- `NODEHUB_DAEMON_PORT`
- `NODEHUB_DAEMON_STATE_DIR`
- `NODEHUB_DAEMON_ENABLE_WORKER`
- `NODEHUB_AXL_NODE_URL`
- `NODEHUB_ROUTER_URL`
- `NODEHUB_WALLET_PRIVATE_KEY`
- `NODEHUB_WALLET_PRIVATE_KEY_PATH`
- `NEXT_PUBLIC_API_BASE_URL`

## One-command Demo

The local demo starts:

- 1 customer AXL node
- 2 worker AXL nodes
- 2 MCP routers
- 3 local daemons (customer, Berlin, Tokyo)
- 1 Next.js dashboard

Start it with:

```bash
./Start
```

Notes:

- The dashboard runs at `http://127.0.0.1:3000`
- The dashboard talks only to the customer daemon at `http://127.0.0.1:8010`
- Each daemon keeps a local signed event log under `platform/demo/runtime/*-state`
- Payments are disabled in this demo; quotes keep payment terms only for future compatibility
- Logs are written to `platform/demo/runtime/logs/`
- Press `Ctrl+C` to stop the full demo
