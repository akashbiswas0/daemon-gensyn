# Two-Laptop Browser Task Demo Summary

## What the current logs mean

- Repeated lines like `Connection from peer ...` on the AXL node logs mean the mesh transport is alive and peers are reaching each other.
- They do **not** prove that discovery or jobs are working.
- The real failure in the old setup was the separate coordination path timing out through router/A2A, which showed up as:
  - `Timeout forwarding to nodehub`
  - `POST /route ... 504`
- That is why nodes could still look healthy on laptop 2 while disappearing from the dashboard on laptop 1.

## Final demo architecture

### Laptop 1
- runs the requester stack only
- runs:
  - dashboard
  - customer daemon
  - one bootstrap/customer AXL node
- does **not** run Berlin or Tokyo demo workers by default anymore

### Laptop 2
- runs one onboarded operator worker only
- runs:
  - one worker AXL node
  - one worker daemon
  - one local browser runtime from `node-nexus-agent`

### Cross-laptop behavior
- discovery and job dispatch use raw AXL `/send` and `/recv`
- the browser task is executed locally on laptop 2 by `node-nexus-agent`
- the worker daemon signs a receipt and sends it back to laptop 1
- laptop 1 stores the result and renders the job report

## Exact startup flow

### Laptop 1
```bash
cd /Users/akash/eth-agent
./Start
```

What to note:
- the printed seed peer, for example:
  - `tls://<LAPTOP1_LAN_IP>:9101`

### Laptop 2
If onboarding has not been done yet:
```bash
cd /path/to/eth-agent
./OnboardWorker --label "Tokyo Worker" --region "tokyo" --country "JP" --payout-wallet 0x... --capabilities "browser_task,http_check" --seed-peer "tls://<LAPTOP1_LAN_IP>:9101" --openai-enabled false
```

If the worker was already onboarded:
```bash
cd /path/to/eth-agent
./Start
```

## What success looks like

### `/nodes`
- laptop 2 worker appears as a live node
- it stays visible across refreshes
- its capabilities include `browser_task`

### `/dashboard`
- active operator count is at least `1`
- the worker region appears in the network view

### `/jobs`
- the worker region is shown as a selectable region tile
- you can select exactly one region
- `Run Browser Task` submits and opens a job report

## Browser task execution path

1. Laptop 1 submits a `browser_task` job from the dashboard
2. Customer daemon sends a raw execution request to laptop 2 over the AXL node
3. Laptop 2 worker daemon receives the request
4. `BrowserTaskPlugin` calls the local browser runtime at:
   - `http://127.0.0.1:8080/mcp/execute`
5. `node-nexus-agent` runs the 0G-backed browser flow
6. Laptop 2 daemon signs an execution receipt
7. Laptop 1 receives it and renders the report

## Common failure checks

### Bootstrap peer
- confirm laptop 2 used the exact seed peer printed by laptop 1
- format:
  - `tls://<LAPTOP1_LAN_IP>:9101`

### 0G env presence
- the worker runtime must have:
  - `ZEROG_API_KEY`
  - `ZEROG_PRIVATE_KEY`
- these are stored in:
  - `platform/operator/runtime/worker.env`

### Node visible in `/nodes`
- if laptop 2 is healthy locally but not visible on laptop 1:
  - click `Discover`
  - refresh `/nodes`
  - confirm laptop 2 is still connected to the bootstrap peer

### Region visible in `/jobs`
- `/jobs` region tiles come from live `/nodes`
- if no region appears:
  - the worker is not currently live from the customer daemon’s perspective

### Browser runtime health
- on laptop 2:
```bash
curl http://127.0.0.1:8080/health
```
- this should return healthy before browser tasks are attempted

## Fast demo checklist

1. Start laptop 1 with `./Start`
2. Copy the seed peer from laptop 1
3. Start laptop 2 worker with `./OnboardWorker ...` or `./Start`
4. Confirm laptop 2 appears in `/nodes`
5. Open `/jobs`
6. Select the worker region
7. Submit `browser_task`
8. Confirm the report opens and includes the worker result
