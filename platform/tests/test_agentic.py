from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from daemon.agents.diagnoser import WorkerDiagnosisAgent
from daemon.agents.planner import RequesterPlannerAgent
from daemon.agents.reporter import ReportSynthesisAgent
from shared.contracts import (
    CapabilityName,
    ExecutionReceipt,
    ExecutionRequest,
    JobPlan,
    PaymentTerms,
    ReservationRole,
    StructuredFailure,
    TaskMeasurement,
    TaskResult,
    VerificationPolicy,
    VerificationReceipt,
    VerificationStatus,
)


def test_planner_fallback_respects_region_and_capability() -> None:
    planner = RequesterPlannerAgent(model_client=None, max_candidates=8, agentic_enabled=True)
    nodes = [
        {
            "peer_id": "peer-berlin",
            "label": "Berlin Worker",
            "region": "berlin",
            "active": True,
            "reputation_score": 1.4,
            "capabilities": [{"name": "http_check"}],
        },
        {
            "peer_id": "peer-tokyo",
            "label": "Tokyo Worker",
            "region": "tokyo",
            "active": True,
            "reputation_score": 1.2,
            "capabilities": [{"name": "http_check"}],
        },
        {
            "peer_id": "peer-dns-only",
            "label": "DNS Worker",
            "region": "berlin",
            "active": True,
            "reputation_score": 2.0,
            "capabilities": [{"name": "dns_check"}],
        },
    ]
    plan = asyncio.run(
        planner.plan(
            job_id="job-1",
            task_type=CapabilityName.HTTP_CHECK,
            target_inputs={"url": "https://example.com"},
            requested_regions=["berlin", "tokyo"],
            discovered_nodes=nodes,
            verifier_count=1,
        )
    )
    assert isinstance(plan, JobPlan)
    assert plan.planner_mode == "deterministic"
    assert plan.selected_primary_peer_ids == ["peer-berlin", "peer-tokyo"]
    assert plan.selected_verifier_peer_ids == []


def test_diagnoser_skips_followups_in_browser_first_mode() -> None:
    agent = WorkerDiagnosisAgent(model_client=None, max_followups=2, agentic_enabled=True)
    calls: list[tuple[str, dict[str, object]]] = []

    async def runner(task_type: CapabilityName, arguments: dict[str, object]) -> TaskResult:
        calls.append((task_type.value, arguments))
        return TaskResult(
            job_id="job-1",
            reservation_id="res-1",
            task_type=task_type,
            node_peer_id="peer-a",
            node_region="berlin",
            success=True,
            measurement=TaskMeasurement(latency_ms=20.0),
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )

    summary = asyncio.run(
        agent.diagnose(
            task_type=CapabilityName.HTTP_CHECK,
            job_id="job-1",
            reservation_id="res-1",
            node_peer_id="peer-a",
            node_region="berlin",
            original_inputs={"url": "https://example.com"},
            failure=StructuredFailure(code="timeout", message="timed out"),
            follow_up_runner=runner,
        )
    )
    assert calls == []
    assert summary.source == "deterministic"
    assert summary.diagnosis


def test_reporter_marks_verifier_mismatch() -> None:
    reporter = ReportSynthesisAgent(model_client=None, agentic_enabled=True)
    request = ExecutionRequest(
        job_id="job-1",
        reservation_id="res-1",
        requester_wallet="0xabc",
        requester_peer_id="customer",
        worker_peer_id="worker-a",
        task_type=CapabilityName.HTTP_CHECK,
        inputs={"url": "https://example.com"},
        role=ReservationRole.PRIMARY,
        verification_policy=VerificationPolicy(verifier_count=1),
        payment=PaymentTerms(currency="USDC", payment_terms="demo"),
    )
    primary_result = TaskResult(
        job_id="job-1",
        reservation_id="res-1",
        task_type=CapabilityName.HTTP_CHECK,
        node_peer_id="worker-a",
        node_region="berlin",
        success=False,
        failure=StructuredFailure(code="timeout", message="timed out"),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    primary_receipt = ExecutionReceipt(
        receipt_id="receipt-1",
        job_id="job-1",
        requester_wallet="0xabc",
        requester_peer_id="customer",
        worker_wallet="0xdef",
        worker_peer_id="worker-a",
        role=ReservationRole.PRIMARY,
        result=primary_result,
        payment=PaymentTerms(currency="USDC", payment_terms="demo"),
    )
    verifier_result = TaskResult(
        job_id="job-1",
        reservation_id="verify-1",
        task_type=CapabilityName.HTTP_CHECK,
        node_peer_id="worker-b",
        node_region="tokyo",
        success=True,
        measurement=TaskMeasurement(status_code=200, response_time_ms=120.0),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    verifier_receipt = VerificationReceipt(
        receipt_id="verify-receipt-1",
        verification_id="verify-1",
        primary_receipt_id="receipt-1",
        verifier_wallet="0xghi",
        verifier_peer_id="worker-b",
        result=verifier_result,
        status=VerificationStatus.MISMATCH,
        notes="Verifier succeeded while primary failed.",
    )
    summary = asyncio.run(
        reporter.summarize(
            job_id="job-1",
            execution_request=request,
            primary_receipts=[primary_receipt],
            verifier_receipts=[verifier_receipt],
            job_plan=None,
            diagnoses=[],
        )
    )
    assert "verifier_mismatch" in summary.report_labels
    assert summary.verifier_summary is not None
