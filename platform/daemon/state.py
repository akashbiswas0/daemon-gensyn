from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from shared.contracts import (
    Attestation,
    DiagnosisSummary,
    ExecutionReceipt,
    ExecutionRequest,
    JobPlan,
    JobStatus,
    LeaseAcceptance,
    LeaseProposal,
    LeaseRelease,
    LocalEventRecord,
    NodeAdvertisement,
    ReportSummary,
    SettlementRecord,
    SignedEnvelope,
    VerificationReceipt,
)


class LocalEventStore:
    def __init__(self, state_dir: str) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.event_log_path = self.state_dir / "events.jsonl"
        self.event_log_path.touch(exist_ok=True)

    def append(self, envelope: SignedEnvelope) -> LocalEventRecord:
        if self.has_event(envelope.event_id):
            for record in self.all_records():
                if record.envelope.event_id == envelope.event_id:
                    return record
        record = LocalEventRecord(envelope=envelope, stored_at=datetime.now(UTC))
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json())
            handle.write("\n")
        return record

    def import_many(self, envelopes: list[SignedEnvelope]) -> list[LocalEventRecord]:
        return [self.append(envelope) for envelope in envelopes]

    def has_event(self, event_id: str) -> bool:
        for record in self.all_records():
            if record.envelope.event_id == event_id:
                return True
        return False

    def all_records(self) -> list[LocalEventRecord]:
        records: list[LocalEventRecord] = []
        with self.event_log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                records.append(LocalEventRecord.model_validate_json(line))
        return records

    def all_envelopes(self) -> list[SignedEnvelope]:
        return [record.envelope for record in self.all_records()]

    def envelopes_by_type(self, event_type: str) -> list[SignedEnvelope]:
        return [envelope for envelope in self.all_envelopes() if envelope.event_type == event_type]

    def latest_node_advertisement_envelopes(self) -> list[SignedEnvelope]:
        latest: dict[str, SignedEnvelope] = {}
        for envelope in self.envelopes_by_type("node_advertisement"):
            advertisement = NodeAdvertisement.model_validate(envelope.payload)
            current = latest.get(advertisement.peer_id)
            if current is None or envelope.timestamp > current.timestamp:
                latest[advertisement.peer_id] = envelope
        return list(latest.values())

    def receipt_by_id(self, receipt_id: str) -> dict[str, Any] | None:
        for envelope in self.all_envelopes():
            if envelope.event_type == "execution_receipt":
                receipt = ExecutionReceipt.model_validate(envelope.payload)
                if receipt.receipt_id == receipt_id:
                    return {"kind": "execution", "envelope": envelope, "receipt": receipt.model_dump(mode="json")}
            if envelope.event_type == "verification_receipt":
                receipt = VerificationReceipt.model_validate(envelope.payload)
                if receipt.receipt_id == receipt_id:
                    return {"kind": "verification", "envelope": envelope, "receipt": receipt.model_dump(mode="json")}
        return None

    def settlements(self) -> list[dict[str, Any]]:
        latest: dict[str, SettlementRecord] = {}
        for envelope in self.all_envelopes():
            if not envelope.event_type.startswith("settlement_"):
                continue
            settlement = SettlementRecord.model_validate(envelope.payload)
            current = latest.get(settlement.settlement_id)
            if current is None or settlement.updated_at >= current.updated_at:
                latest[settlement.settlement_id] = settlement
        items = sorted(latest.values(), key=lambda item: item.updated_at, reverse=True)
        return [item.model_dump(mode="json") for item in items]

    def settlement_by_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        for settlement in self.settlements():
            if settlement["receipt_id"] == receipt_id:
                return settlement
        return None

    def known_nodes(self) -> list[dict[str, Any]]:
        advertisements: dict[str, tuple[NodeAdvertisement, SignedEnvelope]] = {}
        for envelope in self.latest_node_advertisement_envelopes():
            advertisement = NodeAdvertisement.model_validate(envelope.payload)
            advertisements[advertisement.peer_id] = (advertisement, envelope)

        attestations_by_peer: dict[str, list[Attestation]] = defaultdict(list)
        for envelope in self.envelopes_by_type("attestation"):
            attestation = Attestation.model_validate(envelope.payload)
            attestations_by_peer[attestation.subject_peer_id].append(attestation)

        nodes: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for peer_id, (advertisement, envelope) in advertisements.items():
            if not advertisement.capabilities:
                continue
            peer_attestations = attestations_by_peer.get(peer_id, [])
            verified = len([item for item in peer_attestations if item.verdict in {"satisfied", "verified"}])
            mismatches = len([item for item in peer_attestations if item.verdict in {"mismatch", "rejected"}])
            trust_score = max(0.1, 1.0 + (verified * 0.1) - (mismatches * 0.2))
            expires_at = envelope.timestamp + timedelta(seconds=advertisement.ttl_seconds)
            nodes.append(
                {
                    "id": peer_id,
                    "peer_id": peer_id,
                    "owner_wallet": advertisement.wallet_address,
                    "label": advertisement.label,
                    "region": advertisement.region,
                    "country_code": advertisement.country_code,
                    "capabilities": [cap.model_dump(mode="json") for cap in advertisement.capabilities],
                    "max_concurrency": advertisement.max_concurrency,
                    "active": advertisement.active and expires_at >= now,
                    "metadata": advertisement.metadata,
                    "reputation_score": round(trust_score, 2),
                    "attestation_count": len(peer_attestations),
                    "verifier_backed_successes": verified,
                    "mismatch_count": mismatches,
                    "last_seen_at": envelope.timestamp.isoformat(),
                    "payment": advertisement.payment.model_dump(mode="json"),
                }
            )
        nodes.sort(key=lambda item: (not item["active"], item["region"], item["label"]))
        return nodes

    def leases(self) -> list[dict[str, Any]]:
        proposals: dict[str, list[LeaseProposal]] = defaultdict(list)
        acceptances: dict[str, list[LeaseAcceptance]] = defaultdict(list)
        releases: dict[str, LeaseRelease] = {}

        for envelope in self.all_envelopes():
            if envelope.event_type == "lease_proposal":
                proposal = LeaseProposal.model_validate(envelope.payload)
                proposals[proposal.lease_id].append(proposal)
            elif envelope.event_type == "lease_acceptance":
                acceptances[LeaseAcceptance.model_validate(envelope.payload).lease_id].append(
                    LeaseAcceptance.model_validate(envelope.payload)
                )
            elif envelope.event_type == "lease_release":
                release = LeaseRelease.model_validate(envelope.payload)
                releases[release.lease_id] = release

        now = datetime.now(UTC)
        leases: list[dict[str, Any]] = []
        for lease_id, proposal_items in proposals.items():
            proposal = proposal_items[0]
            lease_acceptances = acceptances.get(lease_id, [])
            accepted_peers = [item.worker_peer_id for item in lease_acceptances if item.accepted]
            rejected = [item for item in lease_acceptances if not item.accepted]
            status = "pending"
            if lease_id in releases:
                status = "released"
            elif proposal.ends_at < now:
                status = "expired"
            elif accepted_peers:
                status = "active"
            elif rejected:
                status = "rejected"
            leases.append(
                {
                    "id": lease_id,
                    "status": status,
                    "filters": {"regions": proposal.regions},
                    "capability_name": proposal.capability_name.value,
                    "requested_peer_ids": [item.worker_peer_id for item in proposal_items],
                    "accepted_peer_ids": accepted_peers,
                    "reservation_ids": accepted_peers,
                    "lease_window": {
                        "starts_at": proposal.starts_at.isoformat(),
                        "ends_at": proposal.ends_at.isoformat(),
                    },
                    "payment": proposal.payment.model_dump(mode="json"),
                }
            )
        leases.sort(key=lambda item: item["lease_window"]["ends_at"], reverse=True)
        return leases

    def jobs(self) -> list[dict[str, Any]]:
        requests: dict[str, ExecutionRequest] = {}
        request_submitted_at: dict[str, datetime] = {}
        primary_receipts: dict[str, list[ExecutionReceipt]] = defaultdict(list)
        verification_receipts: dict[str, list[VerificationReceipt]] = defaultdict(list)
        plans: dict[str, JobPlan] = {}

        for envelope in self.all_envelopes():
            if envelope.event_type == "execution_request":
                request = ExecutionRequest.model_validate(envelope.payload)
                requests[request.job_id] = request
                request_submitted_at[request.job_id] = envelope.timestamp
            elif envelope.event_type == "execution_receipt":
                receipt = ExecutionReceipt.model_validate(envelope.payload)
                primary_receipts[receipt.job_id].append(receipt)
            elif envelope.event_type == "verification_receipt":
                receipt = VerificationReceipt.model_validate(envelope.payload)
                verification_receipts[receipt.result.job_id].append(receipt)
            elif envelope.event_type == "job_plan":
                plan = JobPlan.model_validate(envelope.payload)
                plans[plan.job_id] = plan

        jobs: list[dict[str, Any]] = []
        for job_id, request in requests.items():
            receipts = primary_receipts.get(job_id, [])
            status = JobStatus.RUNNING.value
            if receipts:
                status = JobStatus.COMPLETED.value
            submitted_at = request_submitted_at.get(job_id)
            jobs.append(
                {
                    "id": job_id,
                    "task_type": request.task_type.value,
                    "status": status,
                    "regions": plans.get(job_id).requested_regions if job_id in plans else [],
                    "lease_id": request.lease_id,
                    "primary_receipt_count": len(receipts),
                    "verification_receipt_count": len(verification_receipts.get(job_id, [])),
                    "planner_mode": plans.get(job_id).planner_mode if job_id in plans else "deterministic",
                    "submitted_at": submitted_at.isoformat() if submitted_at else None,
                }
            )
        # Newest first: sort by the execution_request envelope timestamp.
        # Fall back to job_id only when timestamps are missing on legacy rows.
        jobs.sort(
            key=lambda item: (item.get("submitted_at") or "", item["id"]),
            reverse=True,
        )
        return jobs

    def job_report(self, job_id: str) -> dict[str, Any] | None:
        execution_request: ExecutionRequest | None = None
        primary_results: list[ExecutionReceipt] = []
        verification_results: list[VerificationReceipt] = []
        job_plan: JobPlan | None = None
        diagnoses: list[DiagnosisSummary] = []
        report_summary: ReportSummary | None = None

        for envelope in self.all_envelopes():
            if envelope.event_type == "execution_request":
                request = ExecutionRequest.model_validate(envelope.payload)
                if request.job_id == job_id:
                    execution_request = request
            elif envelope.event_type == "execution_receipt":
                receipt = ExecutionReceipt.model_validate(envelope.payload)
                if receipt.job_id == job_id:
                    primary_results.append(receipt)
            elif envelope.event_type == "verification_receipt":
                receipt = VerificationReceipt.model_validate(envelope.payload)
                if receipt.result.job_id == job_id:
                    verification_results.append(receipt)
            elif envelope.event_type == "job_plan":
                plan = JobPlan.model_validate(envelope.payload)
                if plan.job_id == job_id:
                    job_plan = plan
            elif envelope.event_type == "diagnosis_generated":
                diagnosis = DiagnosisSummary.model_validate(envelope.payload)
                if diagnosis.job_id == job_id:
                    diagnoses.append(diagnosis)
            elif envelope.event_type == "report_summary_generated":
                summary = ReportSummary.model_validate(envelope.payload)
                if summary.job_id == job_id:
                    report_summary = summary

        if execution_request is None:
            return None

        diagnoses.sort(key=lambda item: (item.node_region, item.node_peer_id, item.reservation_id))
        settlement_by_receipt_id = {item["receipt_id"]: item for item in self.settlements()}
        results = [
            {
                **receipt.result.model_dump(mode="json"),
                "receipt_id": receipt.receipt_id,
                "settlement": settlement_by_receipt_id.get(receipt.receipt_id),
            }
            for receipt in primary_results
        ]
        verification = [
            {
                "status": receipt.status.value,
                "notes": receipt.notes,
                "peer_id": receipt.verifier_peer_id,
                "receipt_id": receipt.receipt_id,
                "settlement": settlement_by_receipt_id.get(receipt.receipt_id),
            }
            for receipt in verification_results
        ]
        success_count = len([item for item in primary_results if item.result.success])
        fallback_summary = f"{success_count}/{max(len(primary_results), 1)} task executions succeeded; {len([item for item in verification_results if item.status.value == 'mismatch'])} verification mismatches detected."
        return {
            "job_id": job_id,
            "status": JobStatus.COMPLETED.value if primary_results else JobStatus.RUNNING.value,
            "results": results,
            "verification": verification,
            "summary": report_summary.final_summary if report_summary else fallback_summary,
            "planner_rationale": job_plan.rationale if job_plan else None,
            "planner_mode": job_plan.planner_mode if job_plan else "deterministic",
            "planner_verification_requested": job_plan.verification_requested if job_plan else False,
            "worker_diagnoses": [item.model_dump(mode="json") for item in diagnoses],
            "final_summary": report_summary.final_summary if report_summary else None,
            "report_confidence": report_summary.confidence if report_summary else None,
            "report_scope": report_summary.issue_scope if report_summary else "inconclusive",
            "report_labels": report_summary.report_labels if report_summary else [],
            "report_source": report_summary.source if report_summary else "deterministic",
            "report_summary_mode": report_summary.summary_mode if report_summary else "compact",
            "verifier_summary": report_summary.verifier_summary if report_summary else None,
            "request": execution_request.model_dump(mode="json"),
        }

    def attestations(self) -> list[dict[str, Any]]:
        items = [Attestation.model_validate(envelope.payload) for envelope in self.envelopes_by_type("attestation")]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return [item.model_dump(mode="json") for item in items]
