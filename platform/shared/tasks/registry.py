from __future__ import annotations

from functools import lru_cache

from shared.contracts import CapabilityName
from shared.tasks.api_call import APICallPlugin
from shared.tasks.browser_task import BrowserTaskPlugin
from shared.tasks.cdn_check import CDNCheckPlugin
from shared.tasks.base import TaskPlugin
from shared.tasks.dns_check import DNSCheckPlugin
from shared.tasks.http_check import HTTPCheckPlugin
from shared.tasks.latency_probe import LatencyProbePlugin
from shared.tasks.ping_check import PingCheckPlugin


class TaskRegistry:
    def __init__(self) -> None:
        self._plugins: dict[CapabilityName, TaskPlugin] = {}

    def register(self, plugin: TaskPlugin) -> None:
        self._plugins[plugin.name] = plugin

    def get(self, name: CapabilityName) -> TaskPlugin:
        return self._plugins[name]

    def all(self) -> list[TaskPlugin]:
        return list(self._plugins.values())


@lru_cache(maxsize=None)
def get_task_registry(node_nexus_agent_enabled: bool = False, node_nexus_agent_url: str = "http://127.0.0.1:8080") -> TaskRegistry:
    registry = TaskRegistry()
    registry.register(HTTPCheckPlugin())
    registry.register(DNSCheckPlugin())
    registry.register(LatencyProbePlugin())
    registry.register(PingCheckPlugin())
    registry.register(APICallPlugin())
    registry.register(CDNCheckPlugin())
    if node_nexus_agent_enabled:
        registry.register(BrowserTaskPlugin(node_nexus_agent_url))
    return registry
