from __future__ import annotations

from datetime import UTC, datetime, timedelta
import asyncio

from daemon.service import DaemonRuntime
from daemon.identity import LocalIdentity
from daemon.state import LocalEventStore
from shared.config import PlatformSettings
from shared.contracts import (
    Attestation,
    CapabilityName,
    DiagnosisSummary,
    ExecutionReceipt,
    ExecutionRequest,
    JobPlan,
    LeaseAcceptance,
    LeaseProposal,
    NodeAdvertisement,
    NodeCapability,
    PaymentTerms,
    ReportSummary,
    ReservationRole,
    SettlementRecord,
    SettlementStatus,
    TaskMeasurement,
    TaskResult,
    VerificationPolicy,
)


def test_signed_envelope_verification_and_known_nodes(tmp_path) -> None:
    store = LocalEventStore(str(tmp_path))
    identity = LocalIdentity.load(state_dir=str(tmp_path), peer_id="peer-a")
    advertisement = NodeAdvertisement(
        peer_id="peer-a",
        wallet_address=identity.wallet_address,
        label="Berlin Worker",
        region="berlin",
        country_code="DE",
        capabilities=[NodeCapability(name=CapabilityName.HTTP_CHECK, description="http", price_per_invocation=0.25)],
        max_concurrency=2,
    )
    envelope = identity.sign_envelope("node_advertisement", advertisement.model_dump(mode="json"))
    assert LocalIdentity.verify_envelope(envelope)
    store.append(envelope)

    attestation = Attestation(
        attestation_id="att-1",
        subject_peer_id="peer-a",
        issuer_wallet=identity.wallet_address,
        issuer_peer_id="peer-a",
        verdict="verified",
        created_at=datetime.now(UTC),
    )
    store.append(identity.sign_envelope("attestation", attestation.model_dump(mode="json")))

    nodes = store.known_nodes()
    assert len(nodes) == 1
    assert nodes[0]["peer_id"] == "peer-a"
    assert nodes[0]["attestation_count"] == 1
    assert nodes[0]["reputation_score"] > 1.0


def test_lease_and_job_report_materialization(tmp_path) -> None:
    store = LocalEventStore(str(tmp_path))
    requester = LocalIdentity.load(state_dir=str(tmp_path / "requester"), peer_id="customer-peer")
    worker = LocalIdentity.load(state_dir=str(tmp_path / "worker"), peer_id="worker-peer")

    proposal = LeaseProposal(
        lease_id="lease-1",
        quote_id="quote-1",
        requester_wallet=requester.wallet_address,
        requester_peer_id=requester.peer_id,
        worker_wallet=worker.wallet_address,
        worker_peer_id=worker.peer_id,
        capability_name=CapabilityName.HTTP_CHECK,
        starts_at=datetime.now(UTC),
        ends_at=datetime.now(UTC) + timedelta(hours=1),
        regions=["berlin"],
        payment=PaymentTerms(quoted_price=0.25, currency="USDC", payment_terms="deferred"),
    )
    store.append(requester.sign_envelope("lease_proposal", proposal.model_dump(mode="json")))
    acceptance = LeaseAcceptance(
        lease_id="lease-1",
        quote_id="quote-1",
        worker_wallet=worker.wallet_address,
        worker_peer_id=worker.peer_id,
        accepted=True,
        accepted_at=datetime.now(UTC),
    )
    store.append(worker.sign_envelope("lease_acceptance", acceptance.model_dump(mode="json")))

    leases = store.leases()
    assert len(leases) == 1
    assert leases[0]["status"] == "active"
    assert leases[0]["accepted_peer_ids"] == ["worker-peer"]

    request = ExecutionRequest(
        job_id="job-1",
        reservation_id="res-1",
        lease_id="lease-1",
        requester_wallet=requester.wallet_address,
        requester_peer_id=requester.peer_id,
        worker_peer_id=worker.peer_id,
        task_type=CapabilityName.HTTP_CHECK,
        inputs={"url": "https://example.com", "method": "GET", "timeout_seconds": 5},
        role=ReservationRole.PRIMARY,
        verification_policy=VerificationPolicy(verifier_count=0),
        payment=PaymentTerms(currency="USDC", payment_terms="deferred"),
    )
    store.append(requester.sign_envelope("execution_request", request.model_dump(mode="json")))
    plan = JobPlan(
        job_id="job-1",
        task_type=CapabilityName.HTTP_CHECK,
        requested_regions=["berlin"],
        selected_primary_peer_ids=[worker.peer_id],
        selected_verifier_peer_ids=[],
        use_lease_backed=True,
        selected_lease_id="lease-1",
        rationale="Used the active Berlin lease.",
        verification_requested=False,
        planner_mode="deterministic",
    )
    store.append(requester.sign_envelope("job_plan", plan.model_dump(mode="json")))
    result = TaskResult(
        job_id="job-1",
        reservation_id="lease-1",
        task_type=CapabilityName.HTTP_CHECK,
        node_peer_id=worker.peer_id,
        node_region="berlin",
        success=True,
        measurement=TaskMeasurement(status_code=200, response_time_ms=120.0),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    receipt = ExecutionReceipt(
        receipt_id="receipt-1",
        job_id="job-1",
        lease_id="lease-1",
        requester_wallet=requester.wallet_address,
        requester_peer_id=requester.peer_id,
        worker_wallet=worker.wallet_address,
        worker_peer_id=worker.peer_id,
        role=ReservationRole.PRIMARY,
        result=result,
        payment=PaymentTerms(currency="USDC", payment_terms="deferred"),
    )
    store.append(worker.sign_envelope("execution_receipt", receipt.model_dump(mode="json")))
    settlement = SettlementRecord(
        settlement_id="execution:receipt-1",
        job_id="job-1",
        receipt_id="receipt-1",
        worker_peer_id=worker.peer_id,
        worker_wallet=worker.wallet_address,
        role=ReservationRole.PRIMARY,
        capability_name=CapabilityName.HTTP_CHECK,
        amount=0.25,
        currency="USDC",
        token_address="0xToken",
        network="base-sepolia",
        status=SettlementStatus.CONFIRMED,
        tx_hash="0xabc123",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    store.append(requester.sign_envelope("settlement_confirmed", settlement.model_dump(mode="json")))
    diagnosis = DiagnosisSummary(
        job_id="job-1",
        reservation_id="lease-1",
        task_type=CapabilityName.HTTP_CHECK,
        node_peer_id=worker.peer_id,
        node_region="berlin",
        diagnosis="HTTP check succeeded without needing follow-up diagnosis.",
        confidence=0.61,
        source="deterministic",
    )
    store.append(requester.sign_envelope("diagnosis_generated", diagnosis.model_dump(mode="json")))
    summary = ReportSummary(
        job_id="job-1",
        final_summary="The Berlin worker completed the HTTP check successfully.",
        confidence=0.77,
        issue_scope="inconclusive",
        verifier_summary=None,
        report_labels=["healthy"],
        source="deterministic",
    )
    store.append(requester.sign_envelope("report_summary_generated", summary.model_dump(mode="json")))

    report = store.job_report("job-1")
    assert report is not None
    assert report["status"] == "completed"
    assert len(report["results"]) == 1
    assert report["results"][0]["measurement"]["status_code"] == 200
    assert report["results"][0]["settlement"]["status"] == "confirmed"
    assert report["planner_rationale"] == "Used the active Berlin lease."
    assert report["final_summary"] == "The Berlin worker completed the HTTP check successfully."
    assert report["worker_diagnoses"][0]["diagnosis"] == "HTTP check succeeded without needing follow-up diagnosis."


def test_attestations_are_backfilled_from_existing_receipts(tmp_path) -> None:
    state_dir = tmp_path / "customer"
    store = LocalEventStore(str(state_dir))
    requester = LocalIdentity.load(state_dir=str(state_dir), peer_id="customer-peer")
    worker = LocalIdentity.load(state_dir=str(tmp_path / "worker"), peer_id="worker-peer")

    result = TaskResult(
        job_id="job-2",
        reservation_id="res-2",
        task_type=CapabilityName.DNS_CHECK,
        node_peer_id=worker.peer_id,
        node_region="berlin",
        success=True,
        measurement=TaskMeasurement(response_time_ms=42.0, resolved_addresses=["1.1.1.1"]),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    receipt = ExecutionReceipt(
        receipt_id="receipt-2",
        job_id="job-2",
        requester_wallet=requester.wallet_address,
        requester_peer_id=requester.peer_id,
        worker_wallet=worker.wallet_address,
        worker_peer_id=worker.peer_id,
        role=ReservationRole.PRIMARY,
        result=result,
        payment=PaymentTerms(currency="USDC", payment_terms="deferred"),
    )
    store.append(worker.sign_envelope("execution_receipt", receipt.model_dump(mode="json")))

    runtime = DaemonRuntime(
        PlatformSettings(
            daemon_state_dir=str(state_dir),
            daemon_enable_worker=False,
        )
    )
    runtime.peer_id = requester.peer_id
    runtime.identity = requester
    runtime.backfill_attestations()

    attestations = store.attestations()
    assert len(attestations) == 1
    assert attestations[0]["subject_peer_id"] == worker.peer_id
    assert attestations[0]["verdict"] == "satisfied"


def test_worker_can_advertise_separate_payout_wallet_and_filtered_capabilities(tmp_path) -> None:
    runtime = DaemonRuntime(
        PlatformSettings(
            daemon_state_dir=str(tmp_path),
            daemon_enable_worker=True,
            worker_payout_wallet="0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
            node_nexus_agent_enabled=True,
            worker_enabled_capabilities=["browser_task"],
        )
    )
    identity = LocalIdentity.load(state_dir=str(tmp_path), peer_id="worker-peer")
    runtime.peer_id = identity.peer_id
    runtime.identity = identity

    advertisement = runtime.current_advertisement()
    assert advertisement.wallet_address == "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
    assert advertisement.wallet_address != identity.wallet_address

    capability_names = [cap.name for cap in runtime.worker_capabilities()]
    assert capability_names == [CapabilityName.BROWSER_TASK]


def test_worker_can_advertise_browser_task_when_node_nexus_is_enabled(tmp_path) -> None:
    runtime = DaemonRuntime(
        PlatformSettings(
            daemon_state_dir=str(tmp_path),
            daemon_enable_worker=True,
            node_nexus_agent_enabled=True,
            worker_enabled_capabilities=["browser_task"],
        )
    )
    identity = LocalIdentity.load(state_dir=str(tmp_path), peer_id="worker-peer")
    runtime.peer_id = identity.peer_id
    runtime.identity = identity

    capability_names = [cap.name for cap in runtime.worker_capabilities()]
    assert CapabilityName.BROWSER_TASK in capability_names


def test_announce_current_advertisement_pushes_to_seed_peers(tmp_path) -> None:
    runtime = DaemonRuntime(
        PlatformSettings(
            daemon_state_dir=str(tmp_path),
            daemon_enable_worker=True,
            node_nexus_agent_enabled=True,
            worker_enabled_capabilities=["browser_task"],
        )
    )
    identity = LocalIdentity.load(state_dir=str(tmp_path), peer_id="worker-peer")
    runtime.peer_id = identity.peer_id
    runtime.identity = identity

    published: list[tuple[str, str]] = []

    async def fake_seed_peer_ids(_: list[str]) -> list[str]:
        return ["peer-a", "peer-b"]

    async def fake_publish(peer_id: str, envelope) -> dict[str, bool]:
        published.append((peer_id, envelope.payload["peer_id"]))
        return {"stored": True}

    runtime.seed_peer_ids = fake_seed_peer_ids  # type: ignore[method-assign]
    runtime.publish_advertisement = fake_publish  # type: ignore[method-assign]

    asyncio.run(runtime.announce_current_advertisement())

    assert published == [("peer-a", "worker-peer"), ("peer-b", "worker-peer")]
    assert any(item["peer_id"] == "worker-peer" for item in runtime.store.known_nodes())


def test_advertise_node_tool_imports_remote_advertisement(tmp_path) -> None:
    runtime = DaemonRuntime(
        PlatformSettings(
            daemon_state_dir=str(tmp_path / "receiver"),
            daemon_enable_worker=False,
        )
    )
    receiver = LocalIdentity.load(state_dir=str(tmp_path / "receiver"), peer_id="receiver-peer")
    sender = LocalIdentity.load(state_dir=str(tmp_path / "sender"), peer_id="sender-peer")
    runtime.peer_id = receiver.peer_id
    runtime.identity = receiver

    advertisement = NodeAdvertisement(
        peer_id=sender.peer_id,
        wallet_address=sender.wallet_address,
        label="Remote Worker",
        region="new-york",
        country_code="US",
        capabilities=[NodeCapability(name=CapabilityName.HTTP_CHECK, description="http", price_per_invocation=0.25)],
        max_concurrency=2,
    )
    envelope = sender.sign_envelope("node_advertisement", advertisement.model_dump(mode="json"))

    result = asyncio.run(
        runtime.handle_nodehub_tool_call("advertise_node", {"envelope": envelope.model_dump(mode="json")})
    )

    assert result == {"stored": True}
    nodes = runtime.store.known_nodes()
    assert any(node["peer_id"] == sender.peer_id and node["region"] == "new-york" for node in nodes)


def test_advertise_node_tool_relays_new_remote_advertisement(tmp_path) -> None:
    runtime = DaemonRuntime(
        PlatformSettings(
            daemon_state_dir=str(tmp_path / "receiver"),
            daemon_enable_worker=False,
        )
    )
    receiver = LocalIdentity.load(state_dir=str(tmp_path / "receiver"), peer_id="receiver-peer")
    sender = LocalIdentity.load(state_dir=str(tmp_path / "sender"), peer_id="sender-peer")
    runtime.peer_id = receiver.peer_id
    runtime.identity = receiver

    advertisement = NodeAdvertisement(
        peer_id=sender.peer_id,
        wallet_address=sender.wallet_address,
        label="Remote Worker",
        region="new-york",
        country_code="US",
        capabilities=[NodeCapability(name=CapabilityName.HTTP_CHECK, description="http", price_per_invocation=0.25)],
        max_concurrency=2,
    )
    envelope = sender.sign_envelope("node_advertisement", advertisement.model_dump(mode="json"))

    published: list[str] = []

    async def fake_seed_peer_ids(_: list[str]) -> list[str]:
        return ["peer-a", sender.peer_id, "peer-b"]

    async def fake_publish(peer_id: str, relay_envelope) -> dict[str, bool]:
        assert relay_envelope.event_id == envelope.event_id
        published.append(peer_id)
        return {"stored": True}

    runtime.seed_peer_ids = fake_seed_peer_ids  # type: ignore[method-assign]
    runtime.publish_advertisement = fake_publish  # type: ignore[method-assign]

    result = asyncio.run(
        runtime.handle_nodehub_tool_call("advertise_node", {"envelope": envelope.model_dump(mode="json")})
    )

    assert result == {"stored": True}
    assert published == ["peer-a", "peer-b"]
