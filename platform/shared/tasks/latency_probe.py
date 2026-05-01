from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from shared.contracts import CapabilityName, LatencyProbeInput, TaskMeasurement, TaskResult
from shared.tasks.base import TaskPlugin


class LatencyProbePlugin(TaskPlugin):
    name = CapabilityName.LATENCY_PROBE
    description = "Measure TCP connect latency to a target host and port."

    async def execute(
        self,
        inputs: dict[str, Any],
        *,
        job_id: str,
        reservation_id: str,
        node_peer_id: str,
        node_region: str,
    ) -> TaskResult:
        payload = LatencyProbeInput.model_validate(inputs)
        started_at = datetime.now(UTC)
        timer = perf_counter()
        try:
            connection = asyncio.open_connection(payload.host, payload.port)
            reader, writer = await asyncio.wait_for(connection, timeout=payload.timeout_seconds)
            elapsed_ms = (perf_counter() - timer) * 1000
            writer.close()
            await writer.wait_closed()
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=True,
                measurement=TaskMeasurement(latency_ms=elapsed_ms),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"connected": True},
            )
        except (asyncio.TimeoutError, OSError) as exc:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure("latency_probe_failed", "Latency probe failed", retryable=True, details={"error": str(exc)}),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"error": str(exc)},
            )

    async def verify(self, primary: TaskResult, candidate: TaskResult) -> tuple[bool, str]:
        if not primary.success or not candidate.success:
            return primary.success == candidate.success, "latency probe failure mismatch"
        left = primary.measurement.latency_ms or 0
        right = candidate.measurement.latency_ms or 0
        if abs(left - right) > 75:
            return False, "latency deviation above tolerance"
        return True, "latency aligned"
