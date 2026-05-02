import logging

from shared.config import get_settings

from daemon.service import create_daemon_app


class _LoopbackProbeFilter(logging.Filter):
    # Drops uvicorn access lines for boring local self-probes (start_worker.sh
    # health checks, the agent-card warm-up, identity reads). Real peer traffic
    # comes through the AXL bridge from non-loopback origins, so it survives.
    _NOISY_PATHS = ("/health", "/identity", "/.well-known/agent-card.json")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        if "127.0.0.1" not in message:
            return True
        return not any(f" {path} " in message for path in self._NOISY_PATHS)


logging.getLogger("uvicorn.access").addFilter(_LoopbackProbeFilter())

app = create_daemon_app(get_settings())
