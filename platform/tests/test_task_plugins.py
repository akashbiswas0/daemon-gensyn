from shared.contracts import CapabilityName
from shared.tasks.cdn_check import CDNCheckPlugin
from shared.tasks.ping_check import PingCheckPlugin
from shared.tasks.registry import get_task_registry


def test_registry_includes_new_global_checks() -> None:
    registry = get_task_registry()
    capability_names = {plugin.name for plugin in registry.all()}
    assert CapabilityName.PING_CHECK in capability_names
    assert CapabilityName.API_CALL in capability_names
    assert CapabilityName.CDN_CHECK in capability_names


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
