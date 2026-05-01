from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from shared.contracts import CapabilityName, PingCheckInput, TaskMeasurement, TaskResult
from shared.tasks.base import TaskPlugin


class PingCheckPlugin(TaskPlugin):
    name = CapabilityName.PING_CHECK
    description = "Run a bounded ICMP ping from the worker node."

    async def execute(
        self,
        inputs: dict[str, Any],
        *,
        job_id: str,
        reservation_id: str,
        node_peer_id: str,
        node_region: str,
    ) -> TaskResult:
        payload = PingCheckInput.model_validate(inputs)
        started_at = datetime.now(UTC)
        timer = perf_counter()
        try:
            process = await asyncio.create_subprocess_exec(
                "ping",
                "-n",
                "-c",
                str(payload.count),
                payload.host,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure("ping_unavailable", "System ping command is unavailable", details={"error": str(exc)}),
                started_at=started_at,
                completed_at=datetime.now(UTC),
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=payload.timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure("ping_timeout", "Ping command timed out", retryable=True),
                started_at=started_at,
                completed_at=datetime.now(UTC),
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        elapsed_ms = (perf_counter() - timer) * 1000
        packet_loss = self._parse_packet_loss(stdout)
        avg_latency = self._parse_average_latency(stdout)

        if process.returncode == 0:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=True,
                measurement=TaskMeasurement(
                    latency_ms=avg_latency,
                    response_time_ms=elapsed_ms,
                    packet_loss_percent=packet_loss,
                ),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"stdout": stdout.strip()},
            )

        return TaskResult(
            job_id=job_id,
            reservation_id=reservation_id,
            task_type=self.name,
            node_peer_id=node_peer_id,
            node_region=node_region,
            success=False,
            measurement=TaskMeasurement(
                latency_ms=avg_latency,
                response_time_ms=elapsed_ms,
                packet_loss_percent=packet_loss,
            ),
            failure=self.failure(
                "ping_failed",
                "Ping command failed",
                retryable=True,
                details={"stderr": stderr.strip(), "stdout": stdout.strip()},
            ),
            started_at=started_at,
            completed_at=datetime.now(UTC),
            raw={"stdout": stdout.strip(), "stderr": stderr.strip()},
        )

    async def verify(self, primary: TaskResult, candidate: TaskResult) -> tuple[bool, str]:
        if primary.success != candidate.success:
            return False, "ping success mismatch"
        left = primary.measurement.latency_ms or 0.0
        right = candidate.measurement.latency_ms or 0.0
        if abs(left - right) > 100:
            return False, "ping latency deviation above tolerance"
        return True, "ping results aligned"

    @staticmethod
    def _parse_packet_loss(output: str) -> float | None:
        match = re.search(r"([\d.]+)% packet loss", output)
        return float(match.group(1)) if match else None

    @staticmethod
    def _parse_average_latency(output: str) -> float | None:
        match = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)/", output)
        return float(match.group(2)) if match else None
