from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from shared.contracts import BrowserTaskInput, CapabilityName, TaskMeasurement, TaskResult
from shared.tasks.base import TaskPlugin


class BrowserTaskPlugin(TaskPlugin):
    name = CapabilityName.BROWSER_TASK
    description = "Run a 0G-backed browser task through the local node-nexus-agent orchestrator."

    def __init__(self, orchestrator_url: str) -> None:
        self.orchestrator_url = orchestrator_url.rstrip("/")

    async def execute(
        self,
        inputs: dict[str, Any],
        *,
        job_id: str,
        reservation_id: str,
        node_peer_id: str,
        node_region: str,
    ) -> TaskResult:
        payload = BrowserTaskInput.model_validate(inputs)
        started_at = datetime.now(UTC)
        endpoint = f"{self.orchestrator_url}/mcp/execute"
        request_body = {
            "url": str(payload.url),
            "task": payload.task,
            "x402_sig": payload.x402_sig,
        }

        try:
            async with httpx.AsyncClient(timeout=660.0) as client:
                response = await client.post(endpoint, json=request_body)
            body = response.json()
        except httpx.TimeoutException as exc:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure("browser_task_timeout", "Browser task timed out", retryable=True),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"error": str(exc), "orchestrator_url": self.orchestrator_url},
            )
        except httpx.HTTPError as exc:
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure(
                    "browser_task_transport_error",
                    "Browser task request failed before the orchestrator responded.",
                    retryable=True,
                    details={"error": str(exc)},
                ),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"error": str(exc), "orchestrator_url": self.orchestrator_url},
            )

        if response.status_code >= 400 or not body.get("ok", False):
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=self.name,
                node_peer_id=node_peer_id,
                node_region=node_region,
                success=False,
                failure=self.failure(
                    "browser_task_failed",
                    body.get("error", "Browser task failed."),
                    retryable=response.status_code >= 500,
                    details={"status_code": response.status_code},
                ),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw={"response": body, "orchestrator_url": self.orchestrator_url},
            )

        return TaskResult(
            job_id=job_id,
            reservation_id=reservation_id,
            task_type=self.name,
            node_peer_id=node_peer_id,
            node_region=node_region,
            success=True,
            measurement=TaskMeasurement(
                resolved_url=str(payload.url),
            ),
            started_at=started_at,
            completed_at=datetime.now(UTC),
            raw={
                "proof_hash": body.get("proofHash"),
                "proof_path": body.get("proofPath"),
                "payment": body.get("payment"),
                "orchestrator_url": self.orchestrator_url,
                "request": request_body,
                "response": body,
            },
        )
