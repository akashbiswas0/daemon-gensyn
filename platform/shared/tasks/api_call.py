from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from shared.contracts import APICallInput, CapabilityName, TaskMeasurement, TaskResult
from shared.tasks.base import TaskPlugin


class APICallPlugin(TaskPlugin):
    name = CapabilityName.API_CALL
    description = "Perform a bounded API request with optional JSON payload."

    async def execute(
        self,
        inputs: dict[str, Any],
        *,
        job_id: str,
        reservation_id: str,
        node_peer_id: str,
        node_region: str,
    ) -> TaskResult:
        payload = APICallInput.model_validate(inputs)
        started_at = datetime.now(UTC)
        timer = perf_counter()
        request_kwargs: dict[str, Any] = {"headers": payload.headers}
        if payload.json_body is not None:
            request_kwargs["json"] = payload.json_body
        elif payload.raw_body is not None:
            request_kwargs["content"] = payload.raw_body

        try:
            async with httpx.AsyncClient(timeout=payload.timeout_seconds, follow_redirects=True) as client:
                response = await client.request(payload.method, str(payload.url), **request_kwargs)
        except httpx.TimeoutException as exc:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure("api_timeout", "API request timed out", retryable=True, details={"error": str(exc)}),
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
                failure=self.failure("api_request_failed", "API request failed", details={"error": str(exc)}),
                started_at=started_at,
                completed_at=datetime.now(UTC),
            )

        elapsed_ms = (perf_counter() - timer) * 1000
        expected = set(payload.expected_statuses)
        success = response.status_code in expected if expected else response.is_success
        preview = response.text[:200] if response.text else ""
        measurement = TaskMeasurement(
            status_code=response.status_code,
            response_time_ms=elapsed_ms,
            resolved_url=str(response.url),
            headers={k: v for k, v in response.headers.items()},
        )
        if success:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=True,
                measurement=measurement,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"text_preview": preview},
            )

        return TaskResult(
            job_id=job_id,
            reservation_id=reservation_id,
            task_type=self.name,
            node_peer_id=node_peer_id,
            node_region=node_region,
            success=False,
            measurement=measurement,
            failure=self.failure(
                "unexpected_status",
                f"API returned status {response.status_code}",
                details={"expected_statuses": payload.expected_statuses},
            ),
            started_at=started_at,
            completed_at=datetime.now(UTC),
            raw={"text_preview": preview},
        )

    async def verify(self, primary: TaskResult, candidate: TaskResult) -> tuple[bool, str]:
        if primary.success != candidate.success:
            return False, "api success mismatch"
        if primary.measurement.status_code != candidate.measurement.status_code:
            return False, "api status code mismatch"
        return True, "api results aligned"
