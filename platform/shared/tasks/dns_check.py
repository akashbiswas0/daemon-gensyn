from __future__ import annotations

import socket
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from shared.contracts import CapabilityName, DNSCheckInput, TaskMeasurement, TaskResult
from shared.tasks.base import TaskPlugin


class DNSCheckPlugin(TaskPlugin):
    name = CapabilityName.DNS_CHECK
    description = "Resolve a hostname from the worker node's local resolver."

    async def execute(
        self,
        inputs: dict[str, Any],
        *,
        job_id: str,
        reservation_id: str,
        node_peer_id: str,
        node_region: str,
    ) -> TaskResult:
        payload = DNSCheckInput.model_validate(inputs)
        started_at = datetime.now(UTC)
        timer = perf_counter()
        try:
            answers = sorted(
                {
                    item[4][0]
                    for item in socket.getaddrinfo(payload.hostname, payload.port, type=socket.SOCK_STREAM)
                }
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
                    dns_answers=answers,
                    response_time_ms=elapsed_ms,
                ),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"answer_count": len(answers)},
            )
        except socket.gaierror as exc:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure("dns_resolution_failed", "DNS resolution failed", details={"error": str(exc)}),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"error": str(exc)},
            )

    async def verify(self, primary: TaskResult, candidate: TaskResult) -> tuple[bool, str]:
        if set(primary.measurement.dns_answers) != set(candidate.measurement.dns_answers):
            return False, "dns answers mismatch"
        return True, "dns answers aligned"
