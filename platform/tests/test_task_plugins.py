import asyncio

import httpx

from shared.contracts import CapabilityName
from shared.tasks.browser_task import BrowserTaskPlugin
from shared.tasks.cdn_check import CDNCheckPlugin
from shared.tasks.ping_check import PingCheckPlugin
from shared.tasks.registry import get_task_registry


def test_registry_includes_new_global_checks() -> None:
    registry = get_task_registry()
    capability_names = {plugin.name for plugin in registry.all()}
    assert CapabilityName.PING_CHECK in capability_names
    assert CapabilityName.API_CALL in capability_names
    assert CapabilityName.CDN_CHECK in capability_names


def test_registry_includes_browser_task_when_enabled() -> None:
    registry = get_task_registry(node_nexus_agent_enabled=True, node_nexus_agent_url="http://127.0.0.1:8080")
    capability_names = {plugin.name for plugin in registry.all()}
    assert CapabilityName.BROWSER_TASK in capability_names


def test_ping_output_parser_extracts_latency_and_loss() -> None:
    output = """PING 1.1.1.1 (1.1.1.1): 56 data bytes
64 bytes from 1.1.1.1: icmp_seq=0 ttl=58 time=6.153 ms
64 bytes from 1.1.1.1: icmp_seq=1 ttl=58 time=6.071 ms

--- 1.1.1.1 ping statistics ---
2 packets transmitted, 2 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 6.071/6.112/6.153/0.041 ms
"""
    assert PingCheckPlugin._parse_packet_loss(output) == 0.0
    assert PingCheckPlugin._parse_average_latency(output) == 6.112


def test_cdn_detector_identifies_known_providers() -> None:
    provider, evidence = CDNCheckPlugin._detect_provider(
        {
            "cf-ray": "abc",
            "cf-cache-status": "HIT",
            "server": "cloudflare",
        }
    )
    assert provider == "Cloudflare"
    assert "cf-ray" in evidence
    assert CDNCheckPlugin._extract_cache_status({"cf-cache-status": "HIT"}) == "HIT"


def test_browser_task_plugin_records_proof_hash(monkeypatch) -> None:
    class DummyResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "ok": True,
                "proofHash": "0g://proof-123",
                "proofPath": "./final_proof.png",
                "payment": {"mode": "mock"},
            }

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "DummyAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict[str, object]) -> DummyResponse:
            assert url == "http://127.0.0.1:8080/mcp/execute"
            assert json["url"] == "https://example.com/"
            assert json["task"] == "Capture the title"
            return DummyResponse()

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    plugin = BrowserTaskPlugin("http://127.0.0.1:8080")
    result = asyncio.run(
        plugin.execute(
            {"url": "https://example.com", "task": "Capture the title"},
            job_id="job-1",
            reservation_id="res-1",
            node_peer_id="peer-a",
            node_region="singapore",
        )
    )

    assert result.success is True
    assert result.raw["proof_hash"] == "0g://proof-123"
