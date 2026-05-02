from __future__ import annotations

from collections import Counter
from typing import Any

from daemon.agents.model_client import OpenAIModelClient
from shared.contracts import DiagnosisSummary, ExecutionReceipt, ExecutionRequest, JobPlan, ReportSummary, VerificationReceipt


class ReportSynthesisAgent:
    def __init__(self, *, model_client: OpenAIModelClient | None, agentic_enabled: bool) -> None:
        self.model_client = model_client
        self.agentic_enabled = agentic_enabled

    async def summarize(
        self,
        *,
        job_id: str,
        execution_request: ExecutionRequest,
        primary_receipts: list[ExecutionReceipt],
        verifier_receipts: list[VerificationReceipt],
        job_plan: JobPlan | None,
        diagnoses: list[DiagnosisSummary],
    ) -> ReportSummary:
        baseline = self._deterministic_summary(
            job_id=job_id,
            execution_request=execution_request,
            primary_receipts=primary_receipts,
            verifier_receipts=verifier_receipts,
            job_plan=job_plan,
            diagnoses=diagnoses,
        )
        if not self.agentic_enabled or self.model_client is None or not self.model_client.enabled:
            return baseline

        suggested = await self.model_client.summarize_report(
            {
                "task_type": execution_request.task_type.value,
                "inputs": execution_request.inputs,
                "planner_rationale": job_plan.rationale if job_plan else None,
                "primary_results": [
                    {
                        "peer_id": receipt.worker_peer_id,
                        "region": receipt.result.node_region,
                        "success": receipt.result.success,
                        "measurement": receipt.result.measurement.model_dump(mode="json"),
                        "failure": receipt.result.failure.model_dump(mode="json") if receipt.result.failure else None,
                    }
                    for receipt in primary_receipts
                ],
                "verifier_results": [
                    {
                        "peer_id": receipt.verifier_peer_id,
                        "region": receipt.result.node_region,
                        "status": receipt.status.value,
                        "success": receipt.result.success,
                    }
                    for receipt in verifier_receipts
                ],
                "diagnoses": [diagnosis.model_dump(mode="json") for diagnosis in diagnoses],
                "baseline": baseline.model_dump(mode="json"),
            }
        )
        if not suggested:
            return baseline
        return ReportSummary(
            job_id=job_id,
            final_summary=self._clip((suggested.get("final_summary") or baseline.final_summary).strip(), 220),
            confidence=max(0.0, min(1.0, float(suggested.get("confidence", baseline.confidence)))),
            issue_scope=suggested.get("issue_scope") or baseline.issue_scope,
            verifier_summary=self._clip(suggested.get("verifier_summary") or baseline.verifier_summary, 120),
            report_labels=self._sanitize_labels(suggested.get("report_labels"), baseline.report_labels),
            source="openai-assisted",
            summary_mode="compact",
        )

    def _deterministic_summary(
        self,
        *,
        job_id: str,
        execution_request: ExecutionRequest,
        primary_receipts: list[ExecutionReceipt],
        verifier_receipts: list[VerificationReceipt],
        job_plan: JobPlan | None,
        diagnoses: list[DiagnosisSummary],
    ) -> ReportSummary:
        success_count = len([item for item in primary_receipts if item.result.success])
        mismatch_count = len([item for item in verifier_receipts if item.status.value == "mismatch"])
        labels: list[str] = []
        if mismatch_count:
            labels.append("verifier_mismatch")
        if all(not item.result.success for item in primary_receipts) and primary_receipts:
            labels.append("primary_failures")
        if any(
            item.result.failure and item.result.failure.code == "timeout"
            for item in primary_receipts
        ):
            labels.append("origin_timeout")

        regions = [item.result.node_region for item in primary_receipts]
        region_outcomes = Counter(
            (item.result.node_region, item.result.success) for item in primary_receipts
        )
        if len({success for _, success in region_outcomes.keys()}) > 1:
            issue_scope = "regional"
            labels.append("regional_issue")
        elif primary_receipts and all(not item.result.success for item in primary_receipts):
            issue_scope = "global"
        else:
            issue_scope = "inconclusive"

        diagnosis_line = self._clip(diagnoses[0].diagnosis, 160) if diagnoses else None
        summary_parts = []
        summary_parts.append(
            f"{success_count}/{max(len(primary_receipts), 1)} succeeded across {', '.join(sorted(set(regions))) or 'selected peers'}."
        )
        if mismatch_count:
            summary_parts.append("Verifier mismatch detected.")
        elif verifier_receipts:
            summary_parts.append("Verifier aligned.")
        else:
            summary_parts.append("No verifier.")
        if diagnosis_line:
            summary_parts.append(diagnosis_line)

        verifier_summary = None
        if verifier_receipts:
            verifier_summary = self._clip(" ; ".join(
                f"{item.verifier_peer_id[:8]} in {item.result.node_region}: {item.status.value}"
                for item in verifier_receipts
            ), 120)
        return ReportSummary(
            job_id=job_id,
            final_summary=self._clip(" ".join(summary_parts).strip(), 220),
            confidence=0.67 if diagnoses or verifier_receipts else 0.54,
            issue_scope=issue_scope,
            verifier_summary=verifier_summary,
            report_labels=sorted(set(labels)),
            source="deterministic",
            summary_mode="compact",
        )

    @staticmethod
    def _sanitize_labels(raw_labels: Any, fallback: list[str]) -> list[str]:
        if not isinstance(raw_labels, list):
            return fallback
        cleaned = [str(label).strip() for label in raw_labels if str(label).strip()]
        return sorted(set(cleaned)) or fallback

    @staticmethod
    def _clip(value: str | None, limit: int) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split())
        if len(text) <= limit:
            return text
        return text[: max(limit - 1, 0)].rstrip() + "…"
