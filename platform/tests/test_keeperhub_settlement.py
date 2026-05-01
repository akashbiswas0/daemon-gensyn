from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from daemon.keeperhub_client import KeeperHubClient
from daemon.identity import LocalIdentity
from daemon.service import DaemonRuntime
from shared.config import PlatformSettings
from shared.contracts import (
    CapabilityName,
    ExecutionReceipt,
    PaymentTerms,
    ReservationRole,
    SettlementRecord,
    SettlementStatus,
    TaskMeasurement,
    TaskResult,
)


def make_settlement() -> SettlementRecord:
    now = datetime.now(UTC)
    return SettlementRecord(
        settlement_id="execution:receipt-1",
        job_id="job-1",
        receipt_id="receipt-1",
        worker_peer_id="worker-peer",
        worker_wallet="0x1234567890abcdef1234567890abcdef12345678",
        role=ReservationRole.PRIMARY,
        capability_name=CapabilityName.HTTP_CHECK,
        amount=0.25,
        currency="USDC",
        token_address="0xToken",
        network="base-sepolia",
        status=SettlementStatus.PENDING,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_keeperhub_client_triggers_and_polls() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else None
        calls.append((request.method, str(request.url), body))
        if request.method == "POST":
            return httpx.Response(200, json={"run_id": "run-1", "status": "triggered"})
        return httpx.Response(200, json={"run": {"status": "confirmed", "txHash": "0xabc"}})

    client = KeeperHubClient(
        enabled=True,
        api_key="secret",
        base_url="https://keeperhub.example",
        workflow_id="workflow-1",
        trigger_url="",
        network="base-sepolia",
        token_address="0xToken",
        transport=httpx.MockTransport(handler),
    )

    payload = await client.trigger_payout(make_settlement())
    assert client.extract_run_id(payload) == "run-1"
    assert calls[0][0] == "POST"
    assert calls[0][2]["input"]["to"] == "0x1234567890abcdef1234567890abcdef12345678"
    assert calls[0][2]["input"]["token_address"] == "0xToken"

    run_payload = await client.poll_run("run-1")
    assert client.extract_status(run_payload) == "confirmed"
    assert client.extract_tx_hash(run_payload) == "0xabc"


@pytest.mark.asyncio
async def test_runtime_creates_and_updates_settlement_records(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"run_id": "run-9", "status": "triggered"})
        return httpx.Response(200, json={"status": "confirmed", "tx_hash": "0xdeadbeef"})

    state_dir = tmp_path / "customer"
    worker = LocalIdentity.load(state_dir=str(tmp_path / "worker"), peer_id="worker-peer")
    requester = LocalIdentity.load(state_dir=str(state_dir), peer_id="customer-peer")
    runtime = DaemonRuntime(
        PlatformSettings(
            daemon_state_dir=str(state_dir),
            daemon_enable_worker=False,
            keeperhub_enabled=True,
            keeperhub_api_key="secret",
            keeperhub_base_url="https://keeperhub.example",
            keeperhub_workflow_id="workflow-1",
            keeperhub_trigger_url="",
            keeperhub_token_address="0xToken",
        ),
        keeperhub_client=KeeperHubClient(
            enabled=True,
            api_key="secret",
            base_url="https://keeperhub.example",
            workflow_id="workflow-1",
            trigger_url="",
            network="base-sepolia",
            token_address="0xToken",
            transport=httpx.MockTransport(handler),
        ),
    )
    runtime.peer_id = requester.peer_id
    runtime.identity = requester

    receipt = ExecutionReceipt(
        receipt_id="receipt-1",
        job_id="job-1",
        requester_wallet=requester.wallet_address,
        requester_peer_id=requester.peer_id,
        worker_wallet=worker.wallet_address,
        worker_peer_id=worker.peer_id,
        role=ReservationRole.PRIMARY,
        result=TaskResult(
            job_id="job-1",
            reservation_id="res-1",
            task_type=CapabilityName.HTTP_CHECK,
            node_peer_id=worker.peer_id,
            node_region="tokyo",
            success=True,
            measurement=TaskMeasurement(status_code=200, response_time_ms=91.0),
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        ),
        payment=PaymentTerms(quoted_price=0.25, currency="USDC", payment_terms="keeperhub-base-sepolia"),
    )
    runtime.store.append(worker.sign_envelope("execution_receipt", receipt.model_dump(mode="json")))

    await runtime.reconcile_pending_settlements()

    settlements = runtime.store.settlements()
    assert len(settlements) == 1
    assert settlements[0]["status"] == "confirmed"
    assert settlements[0]["tx_hash"] == "0xdeadbeef"
    assert settlements[0]["amount"] == 0.25


@pytest.mark.asyncio
async def test_webhook_trigger_marks_settlement_triggered_without_run_id() -> None:
    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization", "")
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"ok": True})

    client = KeeperHubClient(
        enabled=True,
        api_key="secret",
        base_url="https://keeperhub.example",
        workflow_id="workflow-1",
        trigger_url="https://app.keeperhub.com/api/workflows/heyi8bpz77wcp7ivm6y0i/webhook",
        network="base-sepolia",
        token_address="0xToken",
        transport=httpx.MockTransport(handler),
    )

    payload = await client.trigger_payout(make_settlement())
    assert payload == {"ok": True}
    assert captured["url"].endswith("/webhook")
    assert captured["auth"] == "Bearer secret"
    assert '"to":"0x1234567890abcdef1234567890abcdef12345678"' in captured["body"]


@pytest.mark.asyncio
async def test_webhook_reconcile_skips_broken_run_poll_and_recovers_failed_rows(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    state_dir = tmp_path / "customer"
    requester = LocalIdentity.load(state_dir=str(state_dir), peer_id="customer-peer")
    runtime = DaemonRuntime(
        PlatformSettings(
            daemon_state_dir=str(state_dir),
            daemon_enable_worker=False,
            keeperhub_enabled=True,
            keeperhub_api_key="secret",
            keeperhub_base_url="https://app.keeperhub.com/api",
            keeperhub_workflow_id="workflow-1",
            keeperhub_trigger_url="https://app.keeperhub.com/api/workflows/workflow-1/webhook",
            keeperhub_network="base-sepolia",
            keeperhub_token_address="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        ),
        keeperhub_client=KeeperHubClient(
            enabled=True,
            api_key="secret",
            base_url="https://app.keeperhub.com/api",
            workflow_id="workflow-1",
            trigger_url="https://app.keeperhub.com/api/workflows/workflow-1/webhook",
            network="base-sepolia",
            token_address="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            transport=httpx.MockTransport(handler),
        ),
    )
    runtime.peer_id = requester.peer_id
    runtime.identity = requester
    settlement = make_settlement().model_copy(
        update={
            "status": SettlementStatus.FAILED,
            "network": "sepolia",
            "keeperhub_run_id": "run-404",
            "failure_reason": "Client error '404 Not Found' for url 'https://app.keeperhub.com/api/runs/run-404'",
        }
    )
    runtime.store_settlement(settlement)

    async def no_candidates(_: SettlementRecord) -> list[dict[str, object]]:
        return []

    runtime._candidate_token_transfers = no_candidates  # type: ignore[method-assign]
    await runtime.reconcile_pending_settlements()

    reconciled = runtime.store.settlements()[0]
    assert reconciled["status"] == "triggered"
    assert reconciled["network"] == "base-sepolia"
    assert reconciled["keeperhub_run_id"] == "run-404"
    assert reconciled["failure_reason"] is None


@pytest.mark.asyncio
async def test_reconcile_normalizes_legacy_token_address(tmp_path) -> None:
    state_dir = tmp_path / "customer"
    requester = LocalIdentity.load(state_dir=str(state_dir), peer_id="customer-peer")
    runtime = DaemonRuntime(
        PlatformSettings(
            daemon_state_dir=str(state_dir),
            daemon_enable_worker=False,
            keeperhub_enabled=True,
            keeperhub_api_key="secret",
            keeperhub_base_url="https://app.keeperhub.com/api",
            keeperhub_workflow_id="workflow-1",
            keeperhub_trigger_url="https://app.keeperhub.com/api/workflows/workflow-1/webhook",
            keeperhub_network="base-sepolia",
            keeperhub_token_address="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        ),
    )
    runtime.peer_id = requester.peer_id
    runtime.identity = requester
    settlement = make_settlement().model_copy(
        update={
            "network": "base-sepolia",
            "token_address": "0x1c7D4B7dD95E41D4C45517E6E30C151511a0C7238",
            "status": SettlementStatus.TRIGGERED,
        }
    )
    runtime.store_settlement(settlement)

    async def no_candidates(_: SettlementRecord) -> list[dict[str, object]]:
        return []

    runtime._candidate_token_transfers = no_candidates  # type: ignore[method-assign]
    await runtime.reconcile_pending_settlements()

    reconciled = runtime.store.settlements()[0]
    assert reconciled["network"] == "base-sepolia"
    assert reconciled["token_address"] == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


def test_runtime_matches_distinct_transfers_to_distinct_settlements(tmp_path) -> None:
    runtime = DaemonRuntime(PlatformSettings(daemon_state_dir=str(tmp_path), daemon_enable_worker=False))
    now = datetime.now(UTC)
    first = make_settlement().model_copy(update={"created_at": now, "updated_at": now})
    second = make_settlement().model_copy(
        update={
            "settlement_id": "execution:receipt-2",
            "receipt_id": "receipt-2",
            "created_at": now.replace(microsecond=now.microsecond + 1 if now.microsecond < 999999 else now.microsecond),
            "updated_at": now.replace(microsecond=now.microsecond + 1 if now.microsecond < 999999 else now.microsecond),
        }
    )
    candidates = [
        {"tx_hash": "0xaaa", "timestamp": now, "block_number": 1, "log_index": 0},
        {"tx_hash": "0xbbb", "timestamp": now, "block_number": 2, "log_index": 0},
    ]
    matches = runtime._match_settlements_to_transfers([first, second], candidates, set())
    assert [(item[0].receipt_id, item[1]["tx_hash"]) for item in matches] == [
        ("receipt-1", "0xaaa"),
        ("receipt-2", "0xbbb"),
    ]
