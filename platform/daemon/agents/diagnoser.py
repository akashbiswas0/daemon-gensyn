from __future__ import annotations

from typing import Any, Awaitable, Callable

import httpx

from daemon.agents.model_client import OpenAIModelClient
from shared.contracts import CapabilityName, DiagnosisSummary, StructuredFailure, TaskResult

FollowupRunner = Callable[[CapabilityName, dict[str, Any]], Awaitable[TaskResult]]


class WorkerDiagnosisAgent:
    def __init__(
        self,
        *,
        model_client: OpenAIModelClient | None,
        max_followups: int,
        agentic_enabled: bool,
    ) -> None:
        self.model_client = model_client
        self.max_followups = max_followups
        self.agentic_enabled = agentic_enabled

    async def diagnose(
        self,
        *,
        task_type: CapabilityName,
        job_id: str,
        reservation_id: str,
        node_peer_id: str,
        node_region: str,
        original_inputs: dict[str, Any],
        failure: StructuredFailure | None,
        follow_up_runner: FollowupRunner,
    ) -> DiagnosisSummary:
        plan = self._followup_plan(task_type, original_inputs)[: self.max_followups]
        follow_up_results: dict[str, Any] = {}
        for capability, arguments in plan:
            result = await follow_up_runner(capability, arguments)
            follow_up_results[capability.value] = {
                "success": result.success,
                "measurement": result.measurement.model_dump(mode="json"),
                "failure": result.failure.model_dump(mode="json") if result.failure else None,
            }

        baseline = self._deterministic_summary(task_type, original_inputs, failure, follow_up_results)
        if not self.agentic_enabled or self.model_client is None or not self.model_client.enabled:
            return baseline

        payload = {
            "task_type": task_type.value,
            "region": node_region,
            "inputs": self._compact_inputs(original_inputs),
            "failure": failure.model_dump(mode="json") if failure else None,
            "follow_up_results": follow_up_results,
            "baseline": baseline.model_dump(mode="json"),
        }
        suggested = await self.model_client.diagnose_failure(payload)
        if not suggested:
            return baseline
        return DiagnosisSummary(
            job_id=job_id,
            reservation_id=reservation_id,
            task_type=task_type,
            node_peer_id=node_peer_id,
            node_region=node_region,
            diagnosis=(suggested.get("diagnosis") or baseline.diagnosis).strip(),
            confidence=max(0.0, min(1.0, float(suggested.get("confidence", baseline.confidence)))),
            suggested_next_step=suggested.get("suggested_next_step") or baseline.suggested_next_step,
            follow_up_summary=suggested.get("follow_up_summary") or baseline.follow_up_summary,
            follow_up_results=follow_up_results,
            source="openai-assisted",
        )

    def _followup_plan(self, task_type: CapabilityName, original_inputs: dict[str, Any]) -> list[tuple[CapabilityName, dict[str, Any]]]:
        if task_type == CapabilityName.HTTP_CHECK:
            host = httpx.URL(str(original_inputs.get("url", ""))).host or ""
            port = httpx.URL(str(original_inputs.get("url", ""))).port or 443
            return [
                (CapabilityName.DNS_CHECK, {"hostname": host, "port": port}),
                (CapabilityName.LATENCY_PROBE, {"host": host, "port": port, "timeout_seconds": 5}),
            ]
        if task_type == CapabilityName.LATENCY_PROBE:
            return [
                (
                    CapabilityName.DNS_CHECK,
                    {"hostname": str(original_inputs.get("host", "")), "port": int(original_inputs.get("port", 443))},
                )
            ]
        return []

    def _deterministic_summary(
        self,
        task_type: CapabilityName,
        original_inputs: dict[str, Any],
        failure: StructuredFailure | None,
        follow_up_results: dict[str, Any],
    ) -> DiagnosisSummary:
        diagnosis = "No diagnosis available."
        confidence = 0.35
        suggested_next_step = "Retry from another peer or rerun the task later."
        follow_up_summary = None

        dns = follow_up_results.get(CapabilityName.DNS_CHECK.value)
        latency = follow_up_results.get(CapabilityName.LATENCY_PROBE.value)
        failure_code = failure.code if failure else ""

        if dns and not dns["success"]:
            diagnosis = "The hostname did not resolve from this worker, so the failure is likely DNS-related in this region."
            confidence = 0.84
            suggested_next_step = "Check DNS records or compare against another region."
            follow_up_summary = "Follow-up DNS resolution failed on the worker."
        elif latency and not latency["success"]:
            diagnosis = "DNS resolved, but the worker could not establish a fast TCP path, suggesting a network-path or origin reachability issue."
            confidence = 0.76
            suggested_next_step = "Check upstream reachability, firewall rules, or origin health."
            follow_up_summary = "Latency probe failed after DNS succeeded."
        elif failure_code == "timeout":
            diagnosis = "The request timed out even though basic follow-up checks were inconclusive, so the issue may be intermittent upstream latency."
            confidence = 0.58
            suggested_next_step = "Rerun with another verifier or increase timeout slightly."
            follow_up_summary = "The bounded follow-up checks did not isolate a single root cause."
        elif task_type == CapabilityName.DNS_CHECK:
            hostname = str(original_inputs.get("hostname", "the hostname"))
            diagnosis = f"The DNS lookup for {hostname} failed from this worker."
            confidence = 0.74
            suggested_next_step = "Verify authoritative DNS records and compare from another region."
            follow_up_summary = "No deeper follow-up chain is allowed for DNS failures."

        return DiagnosisSummary(
            job_id="",
            reservation_id="",
            task_type=task_type,
            node_peer_id="",
            node_region="",
            diagnosis=diagnosis,
            confidence=confidence,
            suggested_next_step=suggested_next_step,
            follow_up_summary=follow_up_summary,
            follow_up_results=follow_up_results,
            source="deterministic",
        )

    @staticmethod
    def _compact_inputs(original_inputs: dict[str, Any]) -> dict[str, Any]:
        keep_keys = {"url", "hostname", "host", "port", "method", "timeout_seconds"}
        return {key: value for key, value in original_inputs.items() if key in keep_keys}
