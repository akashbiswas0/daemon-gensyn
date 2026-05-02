import logging

from shared.config import get_settings

from daemon.service import create_daemon_app


class _LoopbackProbeFilter(logging.Filter):
    # Drops uvicorn access lines for boring local self-probes (start_worker.sh
    # health checks, the agent-card warm-up, identity reads). Real peer traffic
    # comes through the AXL bridge from non-loopback origins, so it survives.
    _NOISY_PATHS = ("/health", "/identity", "/.well-known/agent-card.json")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True
        client_addr, request_line = str(args[0]), str(args[1])
        if not client_addr.startswith("127.0.0.1"):
            return True
        # request_line looks like 'GET /health HTTP/1.1'
        parts = request_line.split(" ", 2)
        if len(parts) < 2:
            return True
        return parts[1] not in self._NOISY_PATHS


logging.getLogger("uvicorn.access").addFilter(_LoopbackProbeFilter())

app = create_daemon_app(get_settings())
