from __future__ import annotations

from datetime import datetime, UTC
from time import perf_counter
from typing import Any

import httpx

from shared.contracts import CapabilityName, HttpCheckInput, TaskMeasurement, TaskResult
from shared.tasks.base import TaskPlugin


class HTTPCheckPlugin(TaskPlugin):
    name = CapabilityName.HTTP_CHECK
    description = "Perform a bounded HTTP or HEAD request from the worker node."

    async def execute(
        self,
        inputs: dict[str, Any],
        *,
        job_id: str,
        reservation_id: str,
        node_peer_id: str,
        node_region: str,
    ) -> TaskResult:
        payload = HttpCheckInput.model_validate(inputs)
        started_at = datetime.now(UTC)
        timer = perf_counter()
        try:
            async with httpx.AsyncClient(timeout=payload.timeout_seconds, follow_redirects=False) as client:
                response = await client.request(
                    payload.method,
                    str(payload.url),
                    headers=payload.headers,
                )
            elapsed_ms = (perf_counter() - timer) * 1000
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
                    resolved_url=str(response.url),
                    headers={k: v for k, v in response.headers.items()},
                ),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"text_preview": response.text[:200]},
            )
        except httpx.TimeoutException as exc:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure("timeout", "HTTP request timed out", retryable=True),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"error": str(exc)},
            )
        except httpx.HTTPError as exc:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure("http_error", "HTTP request failed", details={"error": str(exc)}),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"error": str(exc)},
            )

    async def verify(self, primary: TaskResult, candidate: TaskResult) -> tuple[bool, str]:
        if primary.success != candidate.success:
            return False, "http success mismatch"
        if primary.measurement.status_code != candidate.measurement.status_code:
            return False, "status code mismatch"
        return True, "http results aligned"
