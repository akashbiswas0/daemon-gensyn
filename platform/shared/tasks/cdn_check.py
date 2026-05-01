from __future__ import annotations

import socket
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from shared.contracts import CDNCheckInput, CapabilityName, TaskMeasurement, TaskResult
from shared.tasks.base import TaskPlugin


class CDNCheckPlugin(TaskPlugin):
    name = CapabilityName.CDN_CHECK
    description = "Inspect live response headers and resolution data for CDN signals."

    async def execute(
        self,
        inputs: dict[str, Any],
        *,
        job_id: str,
        reservation_id: str,
        node_peer_id: str,
        node_region: str,
    ) -> TaskResult:
        payload = CDNCheckInput.model_validate(inputs)
        started_at = datetime.now(UTC)
        timer = perf_counter()
        url = httpx.URL(str(payload.url))
        host = url.host or ""
        port = url.port or (443 if url.scheme == "https" else 80)

        try:
            async with httpx.AsyncClient(timeout=payload.timeout_seconds, follow_redirects=True) as client:
                response = await client.request(payload.method, str(payload.url), headers=payload.headers)
        except httpx.TimeoutException as exc:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure("cdn_timeout", "CDN check timed out", retryable=True, details={"error": str(exc)}),
                started_at=started_at,
                completed_at=datetime.now(UTC),
            )
        except httpx.HTTPError as exc:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure("cdn_request_failed", "CDN check failed", details={"error": str(exc)}),
                started_at=started_at,
                completed_at=datetime.now(UTC),
            )

        elapsed_ms = (perf_counter() - timer) * 1000
        resolved_ips = sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            }
        )
        headers = {k: v for k, v in response.headers.items()}
        provider, evidence = self._detect_provider(headers)
        cache_status = self._extract_cache_status(headers)

        return TaskResult(
            job_id=job_id,
            reservation_id=reservation_id,
            task_type=self.name,
            node_peer_id=node_peer_id,
            node_region=node_region,
            success=True,
            measurement=TaskMeasurement(
                status_code=response.status_code,
                response_time_ms=elapsed_ms,
                dns_answers=resolved_ips,
                resolved_url=str(response.url),
                headers=headers,
                provider=provider,
                cache_status=cache_status,
            ),
            started_at=started_at,
            completed_at=datetime.now(UTC),
            raw={
                "provider_evidence": evidence,
                "host": host,
            },
        )

    async def verify(self, primary: TaskResult, candidate: TaskResult) -> tuple[bool, str]:
        if primary.measurement.provider != candidate.measurement.provider:
            return False, "cdn provider mismatch"
        return True, "cdn results aligned"

    @staticmethod
    def _detect_provider(headers: dict[str, str]) -> tuple[str, list[str]]:
        lowered = {key.lower(): value for key, value in headers.items()}
        evidence: list[str] = []

        def contains(key: str, needle: str) -> bool:
            return needle.lower() in lowered.get(key, "").lower()

        if "cf-ray" in lowered or contains("server", "cloudflare"):
            evidence.extend([key for key in ("cf-ray", "cf-cache-status", "server") if key in lowered])
            return "Cloudflare", evidence
        if "x-amz-cf-id" in lowered or "x-amz-cf-pop" in lowered or contains("via", "cloudfront"):
            evidence.extend([key for key in ("x-amz-cf-id", "x-amz-cf-pop", "via") if key in lowered])
            return "CloudFront", evidence
        if "x-vercel-cache" in lowered or contains("server", "vercel"):
            evidence.extend([key for key in ("x-vercel-cache", "server") if key in lowered])
            return "Vercel Edge", evidence
        if "x-fastly-request-id" in lowered or contains("x-served-by", "cache-"):
            evidence.extend([key for key in ("x-fastly-request-id", "x-served-by") if key in lowered])
            return "Fastly", evidence
        if "akamai-cache-status" in lowered or contains("server", "akamai"):
            evidence.extend([key for key in ("akamai-cache-status", "server") if key in lowered])
            return "Akamai", evidence
        return "Unknown", evidence

    @staticmethod
    def _extract_cache_status(headers: dict[str, str]) -> str | None:
        lowered = {key.lower(): value for key, value in headers.items()}
        for key in ("cf-cache-status", "x-vercel-cache", "x-cache", "akamai-cache-status"):
            if key in lowered:
                return lowered[key]
        return None
