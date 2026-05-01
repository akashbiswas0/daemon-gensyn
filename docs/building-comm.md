# Building Applications & Examples

## Overview

AXL is a peer-to-peer networking layer that lets you build distributed applications over a mesh of connected nodes.&#x20;

{% hint style="info" %}
For more on the internals of AXL, check out [How it Works.](https://docs.gensyn.ai/tech/agent-exchange-layer/how-it-works)
{% endhint %}

It provides low-level messaging primitives along with higher-level protocol support for MCP and A2A, so you can go from simple fire-and-forget communication to fully discoverable agent services with minimal setup.

### Building your Own Application

Building an application using AXL means picking a starting point. In this case, that starting point can be a *building pattern* which makes use of AXL's low-level functionalities in a particular way.&#x20;

These patterns include **\[1]** fire-and-forget (using `send`/`recv`), **\[2]** MCP services (`request`/`response`), and **\[3]** A2A (agent-to-agent).&#x20;

### Pattern 1: Send/Recv (Fire-and-Forget)

This is the simplest pattern. Your application sends raw bytes and polls for incoming messages.

```python
import requests, json, time

AXL = "http://127.0.0.1:9002"
PEER = "1ee862344fb283395143ac9775150d2e5936efd6e78ed0db83e3f290d3d539ef"

def send(message):
    requests.post(f"{AXL}/send",
        headers={"X-Destination-Peer-Id": PEER},
        data=json.dumps(message))

def recv_loop():
    while True:
        resp = requests.get(f"{AXL}/recv")
        if resp.status_code == 200:
            sender = resp.headers.get("X-From-Peer-Id")
            print(f"From {sender[:8]}...: {resp.text}")
        time.sleep(0.2)
```

* **When to use:** Simple messaging, notifications, data streaming, custom protocols where you control both sides.
* **Limitation:** No built-in acknowledgment. If you need request-response, use MCP/A2A or build correlation over `send`/`recv`.

### Pattern 2: MCP Services (Request-Response)

MCP (Model Context Protocol) gives you structured JSON-RPC request-response. You expose a named service on your node, and other nodes call it remotely.

The requests flow like this:

```
Remote node calls POST /mcp/{your_key}/sentiment
  -> Your node receives it
  -> Multiplexer sees "service" field → forwards to MCP Router (localhost:9003)
  -> Router dispatches to your service (localhost:7100)
  -> Your service processes and responds
  -> Response flows back to remote node
```

#### Step 1: Write Your Service

You can start by configuring a basic HTTP server:

```python
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/mcp", methods=["POST"])
def handle():
    req = request.json
    if req.get("method") == "tools/list":
        return jsonify({
            "jsonrpc": "2.0", "id": req["id"],
            "result": {"tools": [{"name": "analyze", "description": "Analyze sentiment"}]}
        })
    if req.get("method") == "tools/call":
        result = do_analysis(req["params"].get("arguments", {}))
        return jsonify({
            "jsonrpc": "2.0", "id": req["id"],
            "result": {"content": [{"type": "text", "text": json.dumps(result)}]}
        })
    return jsonify({"error": "unknown method"}), 400

app.run(host="127.0.0.1", port=7100)
```

#### Step 2: Start the MCP Router

```bash
cd integrations
pip install -e .
python -m mcp_routing.mcp_router --port 9003
```

#### Step 3: Register your Service w/ Router

```python
requests.post("http://127.0.0.1:9003/register", json={
    "service": "sentiment",
    "endpoint": "http://127.0.0.1:7100/mcp"
})
```

Don't forget to deregister on shutdown using this command:

`requests.delete("http://127.0.0.1:9003/register/sentiment")`

#### Step 4: Enable MCP (Node Config)

If you run this command, any node on the network can call your service by your public key and service name:

```json
{
  "router_addr": "http://127.0.0.1",
  "router_port": 9003
}
```

If you want to calling a remote MCP service (from another node), you'd run this:

```bash
# List tools on a remote peer's "sentiment" service
curl -X POST http://127.0.0.1:9002/mcp/{peer_id}/sentiment \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1,"params":{}}'

# Call a specific tool
curl -X POST http://127.0.0.1:9002/mcp/{peer_id}/sentiment \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"analyze","arguments":{"market":"0x3f"}}}'
```

{% hint style="info" %}
Replace `{peer_id}` with the remote node's 64-character hex public key.&#x20;

Both nodes must share at least one common peer but they don't need direct connectivity.
{% endhint %}

#### **MCP Router Endpoints**

Use this list of router endpoints.

| **Endpoint**                 | **Description**                                                |
| ---------------------------- | -------------------------------------------------------------- |
| `POST /route`                | Forward a request to a registered service (called by the node) |
| `POST /register`             | Register a service: `{"service": "...", "endpoint": "..."}`    |
| `DELETE /register/{service}` | Remove a service                                               |
| `GET /services`              | List registered services                                       |
| `GET /health`                | Health check                                                   |

### Pattern 3: A2A (Agent-to-Agent)

A2A wraps your MCP services as [A2A skills](https://github.com/google/A2A), making them discoverable by A2A-compatible agents.

Run this command:

```bash
python -m a2a_serving.a2a_server --port 9004 --router http://127.0.0.1:9003
```

Then add it to your node configuration file:

```json
{
  "a2a_addr": "http://127.0.0.1",
  "a2a_port": 9004
}
```

The A2A server auto-discovers services from the MCP router and advertises them at `/.well-known/agent.json`.&#x20;

Remote nodes can interact with your A2A server like this:

```bash
# Fetch the remote peer's agent card (discover available skills)
curl http://127.0.0.1:9002/a2a/{peer_id}

# Send an A2A request
curl -X POST http://127.0.0.1:9002/a2a/{peer_id} \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "id": 1,
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "{\"service\":\"sentiment\",\"request\":{\"jsonrpc\":\"2.0\",\"method\":\"tools/list\",\"id\":1,\"params\":{}}}"}],
        "messageId": "msg-001"
      }
    }
  }'
```

The `messageId` is a client-assigned correlation ID. The text part must be a JSON-stringified MCP request matching the format the A2A server expects.

#### A2A Test Client

&#x20;A convenience script is included at `examples/python-client/a2a_client.py`:

```bash
# Local mode (talk to your own A2A server)
python examples/python-client/a2a_client.py --service sentiment --method tools/list

# Remote mode (route through the mesh to a remote peer)
python examples/python-client/a2a_client.py \
  --remote --peer-id {peer_id} \
  --service sentiment --method tools/list
```

### Adding a Custom Protocol

If MCP and A2A don't fit your needs, you can add your own protocol by implementing the `Stream` interface:

```go
type MyStream struct{}

func (s *MyStream) GetID() string { return "my-protocol" }

func (s *MyStream) IsAllowed(data []byte, metadata any) bool {
    var envelope map[string]interface{}
    if err := json.Unmarshal(data, &envelope); err != nil {
        return false
    }
    _, ok := envelope["my_protocol"]
    return ok
}

func (s *MyStream) Forward(metadata any, fromPeerId string) ([]byte, error) {
    // Process the message, return a response
    return responseBytes, nil
}
```

Register it in `internal/tcp/listen/listener.go` alongside the MCP and A2A streams. Messages matching your discriminator will be routed to your handler instead of the default queue.

### Sharing Your Service

Once running, other nodes need two things: **\[1]** your public key (so other nodes can find and connect to yours) and **\[2]** your service name, so they know what to call.&#x20;

You can share your public key and service name however you like.&#x20;

> *e.g., "I'm `37227e...` and I run a `sentiment` MCP service."*

### Built-in Examples

There are several example applications that are built into the AXL repository itself, each demonstrating an angle of the technology. You can find them here.

#### 1. Tensor Exchange

Send and receive PyTorch tensors between nodes using msgpack serialization.

> **File:** `examples/python-client/client.py`

**Modes:**

* `recv`: listen for incoming tensors
* `tensor`: send a tensor to a peer
* `bandwidth`: bandwidth test

```bash
pip3 install -r examples/python-client/requirements.txt

# On the receiving node
python3 examples/python-client/client.py recv --port 9002

# On the sending node
python3 examples/python-client/client.py tensor --port 9012 --peer <PEER_KEY>
```

#### 2. Remote MCP Server

Connect two nodes so one can call MCP tools hosted on the other. A2A is not required. The node's `/mcp/` endpoint talks directly to a remote peer's MCP router.

1. **Remote Machine (Sender)**

```bash
./node -config node-config.json

# Start the MCP router
python -m mcp_routing.mcp_router

# Start your MCP service(s) and register them with the router
```

2. **Local Machine (Receiver)**

```bash
./node -config node-config.json

# List tools on the remote peer's "weather" service
curl -X POST http://127.0.0.1:9002/mcp/<remote-public-key>/weather \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1,"params":{}}'
```

Both nodes must be able to reach at least one common peer (configured in `Peers`). They don't need direct connectivity.

#### 3. Remote A2A

Optimize integration by transforming MCP services into A2A skills using the optional A2A extension.&#x20;

1. **Remote Machine (Sender):**

```bash
python -m a2a_serving.a2a_server
```

2. **Local Machine (Receiver):**

```bash
python examples/python-client/a2a_client.py \
  --remote --peer-id <remote-public-key> \
  --service weather --method tools/list
```

The A2A server automatically detects and registers MCP services as skills, making access easy for agents that are already A2A-compatible.

#### 4. GossipSub

GossipSub-style pub/sub message propagation with IHAVE/IWANT lazy forwarding, built on `send`/`recv`.

> **File:** `examples/python-client/gossipsub/gossipsub.py`

#### 5. Convergecast

Tree-based data aggregation using the network's spanning tree. Nodes derive their position from `/topology` and aggregate results upward toward the root.

> **File:** `examples/python-client/convergecast.py`


---

# Agent Instructions: Querying This Documentation

If you need additional information that is not directly available in this page, you can query the documentation dynamically by asking a question.

Perform an HTTP GET request on the current page URL with the `ask` query parameter:

```
GET https://docs.gensyn.ai/tech/agent-exchange-layer/examples-and-building.md?ask=<question>
```

The question should be specific, self-contained, and written in natural language.
The response will contain a direct answer to the question and relevant excerpts and sources from the documentation.

Use this mechanism when the answer is not explicitly present in the current page, you need clarification or additional context, or you want to retrieve related documentation sections.
