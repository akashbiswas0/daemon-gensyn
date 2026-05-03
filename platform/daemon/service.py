from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from eth_account import Account
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from daemon.agents import (
    OpenAIModelClient,
    ReportSynthesisAgent,
    RequesterPlannerAgent,
    WorkerDiagnosisAgent,
)
from daemon.identity import LocalIdentity
from daemon.state import LocalEventStore
from shared.config import PlatformSettings, get_settings
from shared.contracts import (
    Attestation,
    CapabilityName,
    DiagnosisSummary,
    ExecutionReceipt,
    ExecutionRequest,
    JobPlan,
    NodeAdvertisement,
    NodeCapability,
    PaymentTerms,
    QuoteOffer,
    QuoteRequest,
    ReservationRole,
    SettlementRecord,
    SettlementStatus,
    SignedEnvelope,
    StructuredFailure,
    TaskResult,
    VerificationPolicy,
    VerificationReceipt,
    VerificationRequest,
    VerificationStatus,
)
from shared.tasks import get_task_registry

logger = logging.getLogger(__name__)
NATIVE_TOKEN_DECIMALS = 18
NATIVE_TRANSFER_GAS = 21000


class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    id: str | int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class DiscoverRequest(BaseModel):
    peer_ids: list[str] = Field(default_factory=list)
    depth: int = 1


class JobRequestPayload(BaseModel):
    task_type: CapabilityName
    inputs: dict[str, Any]
    regions: list[str] = Field(default_factory=list)
    verifier_count: int = 1


class ImportAttestationsPayload(BaseModel):
    envelopes: list[SignedEnvelope]


class DemoTransportMessage(BaseModel):
    protocol: str = "nodehub-demo"
    kind: str
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    reply_to_peer_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class DaemonRuntime:
    def __init__(self, settings: PlatformSettings) -> None:
        self.settings = settings
        self.store = LocalEventStore(settings.daemon_state_dir)
        self.registry = get_task_registry(
            node_nexus_agent_enabled=self.settings.node_nexus_agent_enabled,
            node_nexus_agent_url=self.settings.node_nexus_agent_url,
        )
        self.peer_id = ""
        self.identity: LocalIdentity | None = None
        self.reconcile_task: asyncio.Task[None] | None = None
        self.model_client = OpenAIModelClient(
            api_key=self.settings.openai_api_key,
            model=self.settings.openai_model,
        )
        self.planner = RequesterPlannerAgent(
            model_client=self.model_client,
            max_candidates=self.settings.agent_max_candidates,
            agentic_enabled=self.settings.agentic_enabled,
        )
        self.diagnoser = WorkerDiagnosisAgent(
            model_client=self.model_client,
            max_followups=self.settings.agent_max_followups,
            agentic_enabled=self.settings.agentic_enabled,
        )
        self.reporter = ReportSynthesisAgent(
            model_client=self.model_client,
            agentic_enabled=self.settings.agentic_enabled,
        )
        self._pending_demo_requests: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._seen_discover_requests: set[str] = set()

    async def startup(self) -> None:
        self.peer_id = await self.get_peer_id()
        self.identity = LocalIdentity.load(
            state_dir=self.settings.daemon_state_dir,
            peer_id=self.peer_id,
            private_key=self.settings.wallet_private_key,
            private_key_path=self.settings.wallet_private_key_path,
        )
        self.store.append(self.current_advertisement_envelope())
        self.backfill_attestations()
        await self.reconcile_pending_settlements()
        if self.settings.daemon_enable_worker:
            await self.register_with_router()

    async def get_peer_id(self) -> str:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.settings.axl_node_url}/topology")
            response.raise_for_status()
            return response.json()["our_public_key"]

    async def get_topology(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.settings.axl_node_url}/topology")
            response.raise_for_status()
            return response.json()

    async def send_raw_message(self, peer_id: str, message: DemoTransportMessage) -> None:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.settings.axl_node_url}/send",
                    content=message.model_dump_json().encode("utf-8"),
                    headers={
                        "X-Destination-Peer-Id": peer_id,
                        "Content-Type": "application/octet-stream",
                    },
                )
                response.raise_for_status()
            logger.info(
                "raw send ok kind=%s peer=%s request_id=%s",
                message.kind,
                peer_id[:12],
                message.request_id[:8],
            )
        except Exception as exc:
            logger.warning(
                "raw send FAILED kind=%s peer=%s request_id=%s err=%s",
                message.kind,
                peer_id[:12],
                message.request_id[:8],
                exc,
            )
            raise

    async def send_demo_request(
        self,
        peer_id: str,
        *,
        kind: str,
        payload: dict[str, Any],
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        message = DemoTransportMessage(
            kind=kind,
            reply_to_peer_id=self.peer_id,
            payload=payload,
        )
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_demo_requests[message.request_id] = future
        try:
            await self.send_raw_message(peer_id, message)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_demo_requests.pop(message.request_id, None)

    async def poll_recv_once(self) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.settings.axl_node_url}/recv")
        if response.status_code == 204:
            return False
        response.raise_for_status()
        from_peer_id = response.headers.get("X-From-Peer-Id")
        if not from_peer_id:
            return False
        try:
            message = DemoTransportMessage.model_validate_json(response.content)
        except Exception as exc:
            logger.warning("recv: failed to decode demo message from peer=%s err=%s", from_peer_id[:12], exc)
            return True
        if message.protocol != "nodehub-demo":
            logger.info("recv: ignoring foreign protocol=%s from peer=%s", message.protocol, from_peer_id[:12])
            return True
        logger.info(
            "recv ok kind=%s from=%s request_id=%s",
            message.kind,
            from_peer_id[:12],
            message.request_id[:8],
        )
        try:
            await self.handle_demo_message(from_peer_id, message)
        except Exception as exc:
            logger.exception("handle_demo_message FAILED kind=%s from=%s err=%s", message.kind, from_peer_id[:12], exc)
        return True

    async def recv_loop(self) -> None:
        logger.info("recv_loop: starting (axl=%s)", self.settings.axl_node_url)
        while True:
            try:
                handled = await self.poll_recv_once()
            except Exception as exc:
                logger.debug("recv_loop poll error: %s", exc)
                handled = False
            if not handled:
                await asyncio.sleep(0.5)

    async def handle_demo_message(self, from_peer_id: str, message: DemoTransportMessage) -> None:
        if message.kind == "discover_response":
            envelopes = [SignedEnvelope.model_validate(item) for item in message.payload.get("envelopes", [])]
            for envelope in envelopes:
                self.verify_envelope(envelope)
            self.store.import_many(envelopes)
            return

        if message.kind == "node_advertisement":
            envelope = SignedEnvelope.model_validate(message.payload["envelope"])
            await self.store_and_relay_advertisement(envelope, from_peer_id=from_peer_id)
            return

        if message.kind in {"execution_receipt", "verification_receipt", "attestation_ack", "fetch_receipt_response"}:
            future = self._pending_demo_requests.get(message.request_id)
            if future is not None and not future.done():
                future.set_result(message.payload)
            return

        if message.kind == "discover_request":
            if message.request_id in self._seen_discover_requests:
                return
            self._seen_discover_requests.add(message.request_id)
            self.store.append(self.current_advertisement_envelope())
            if message.reply_to_peer_id:
                response = DemoTransportMessage(
                    kind="discover_response",
                    request_id=message.request_id,
                    payload={
                        "envelopes": [
                            envelope.model_dump(mode="json")
                            for envelope in self.store.latest_node_advertisement_envelopes()
                        ]
                    },
                )
                try:
                    await self.send_raw_message(message.reply_to_peer_id, response)
                except Exception:
                    pass
            depth = int(message.payload.get("depth", 0) or 0)
            if depth > 1:
                relay_peer_ids = [
                    peer_id
                    for peer_id in await self.seed_peer_ids([])
                    if peer_id not in {self.peer_id, from_peer_id, message.reply_to_peer_id}
                ]
                forwarded = DemoTransportMessage(
                    kind="discover_request",
                    request_id=message.request_id,
                    reply_to_peer_id=message.reply_to_peer_id,
                    payload={"depth": depth - 1},
                )
                for peer_id in relay_peer_ids:
                    try:
                        await self.send_raw_message(peer_id, forwarded)
                    except Exception:
                        continue
            return

        if message.kind == "execution_request":
            envelope = SignedEnvelope.model_validate(message.payload["envelope"])
            result = await self.handle_execution_request(envelope, verification=False)
            if message.reply_to_peer_id:
                await self.send_raw_message(
                    message.reply_to_peer_id,
                    DemoTransportMessage(
                        kind="execution_receipt",
                        request_id=message.request_id,
                        payload={"envelope": result.model_dump(mode="json")},
                    ),
                )
            return

        if message.kind == "verification_request":
            envelope = SignedEnvelope.model_validate(message.payload["envelope"])
            result = await self.handle_execution_request(envelope, verification=True)
            if message.reply_to_peer_id:
                await self.send_raw_message(
                    message.reply_to_peer_id,
                    DemoTransportMessage(
                        kind="verification_receipt",
                        request_id=message.request_id,
                        payload={"envelope": result.model_dump(mode="json")},
                    ),
                )
            return

        if message.kind == "attestation":
            envelope = SignedEnvelope.model_validate(message.payload["envelope"])
            result = await self.handle_attestation(envelope)
            if message.reply_to_peer_id:
                await self.send_raw_message(
                    message.reply_to_peer_id,
                    DemoTransportMessage(
                        kind="attestation_ack",
                        request_id=message.request_id,
                        payload={"envelope": result.model_dump(mode="json")},
                    ),
                )
            return

        if message.kind == "fetch_receipt_request" and message.reply_to_peer_id:
            receipt = self.store.receipt_by_id(message.payload["receipt_id"]) or {}
            await self.send_raw_message(
                message.reply_to_peer_id,
                DemoTransportMessage(
                    kind="fetch_receipt_response",
                    request_id=message.request_id,
                    payload=receipt,
                ),
            )

    async def register_with_router(self) -> None:
        endpoint = f"http://{self.settings.daemon_host}:{self.settings.daemon_port}/mcp"
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{self.settings.router_url}/register",
                json={"service": self.settings.worker_service_name, "endpoint": endpoint},
            )

    async def deregister_from_router(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for service_name in (self.settings.worker_service_name,):
                try:
                    await client.delete(f"{self.settings.router_url}/register/{service_name}")
                except Exception:
                    continue

    def assert_identity(self) -> LocalIdentity:
        if self.identity is None:
            raise RuntimeError("identity not initialized")
        return self.identity

    def worker_capabilities(self) -> list[NodeCapability]:
        if not self.settings.daemon_enable_worker:
            return []
        enabled_capabilities = self.enabled_worker_capability_names()
        capabilities: list[NodeCapability] = []
        capabilities.extend(
            NodeCapability(
                name=plugin.name,
                description=plugin.description,
                price_per_invocation=self.capability_price(plugin.name),
            )
            for plugin in self.registry.all()
            if plugin.name in enabled_capabilities
        )
        return capabilities

    def capability_price(self, capability_name: CapabilityName) -> float:
        if capability_name == CapabilityName.HTTP_CHECK:
            return self.settings.worker_price_http_check
        if capability_name == CapabilityName.BROWSER_TASK:
            return self.settings.worker_price_browser_task
        return 0.0

    def public_wallet_address(self) -> str:
        identity = self.assert_identity()
        return self.settings.worker_payout_wallet or identity.wallet_address

    def enabled_worker_capability_names(self) -> set[CapabilityName]:
        available = {plugin.name for plugin in self.registry.all()}
        if not self.settings.worker_enabled_capabilities:
            return available
        enabled: set[CapabilityName] = set()
        for item in self.settings.worker_enabled_capabilities:
            try:
                capability = CapabilityName(item)
            except ValueError:
                continue
            if capability in available:
                enabled.add(capability)
        return enabled or available

    def current_advertisement(self) -> NodeAdvertisement:
        identity = self.assert_identity()
        payment_mode = self.payment_mode()
        return NodeAdvertisement(
            peer_id=identity.peer_id,
            wallet_address=self.public_wallet_address(),
            label=self.settings.worker_public_label,
            region=self.settings.worker_region,
            country_code=self.settings.worker_country_code,
            capabilities=self.worker_capabilities(),
            max_concurrency=self.settings.worker_capacity,
            active=self.settings.daemon_enable_worker,
            metadata={
                "agentic": True,
                "agentic_enabled": self.settings.agentic_enabled,
                "agent_model": self.settings.openai_model if self.settings.openai_api_key else "deterministic-fallback",
                "payment_mode": payment_mode,
                "local_daemon": True,
            },
            payment=PaymentTerms(
                quoted_price=None,
                currency="USDC",
                payment_terms=payment_mode,
            ),
        )

    def current_advertisement_envelope(self) -> SignedEnvelope:
        identity = self.assert_identity()
        return identity.sign_envelope("node_advertisement", self.current_advertisement().model_dump(mode="json"))

    def sign_event(self, event_type: str, payload: dict[str, Any]) -> SignedEnvelope:
        return self.assert_identity().sign_envelope(event_type, payload)

    def verify_envelope(self, envelope: SignedEnvelope) -> None:
        if not LocalIdentity.verify_envelope(envelope):
            raise HTTPException(status_code=400, detail="invalid signed envelope")

    def existing_attestation_ids(self) -> set[str]:
        return {
            Attestation.model_validate(envelope.payload).attestation_id
            for envelope in self.store.envelopes_by_type("attestation")
        }

    def make_execution_attestation(self, receipt: ExecutionReceipt) -> Attestation:
        verdict = "satisfied" if receipt.result.success else "rejected"
        status = "succeeded" if receipt.result.success else "failed"
        return Attestation(
            attestation_id=f"execution:{receipt.receipt_id}",
            subject_peer_id=receipt.worker_peer_id,
            issuer_wallet=self.assert_identity().wallet_address,
            issuer_peer_id=self.peer_id,
            job_id=receipt.job_id,
            receipt_id=receipt.receipt_id,
            verdict=verdict,
            notes=f"{receipt.result.task_type.value} {status} in {receipt.result.node_region}.",
            created_at=datetime.now(UTC),
        )

    def make_verification_attestation(
        self,
        receipt: VerificationReceipt,
        *,
        primary_subject_peer_id: str | None = None,
    ) -> Attestation:
        verdict = "verified" if receipt.status == VerificationStatus.VERIFIED else "mismatch"
        subject_peer_id = primary_subject_peer_id or receipt.verifier_peer_id
        note = "Verifier confirmed the primary result." if receipt.status == VerificationStatus.VERIFIED else "Verifier disagreed with the primary result."
        return Attestation(
            attestation_id=f"verification:{receipt.receipt_id}",
            subject_peer_id=subject_peer_id,
            issuer_wallet=self.assert_identity().wallet_address,
            issuer_peer_id=self.peer_id,
            job_id=receipt.result.job_id,
            receipt_id=receipt.receipt_id,
            verdict=verdict,
            notes=note,
            created_at=datetime.now(UTC),
        )

    async def store_attestation(self, attestation: Attestation) -> None:
        if attestation.attestation_id in self.existing_attestation_ids():
            return
        envelope = self.sign_event("attestation", attestation.model_dump(mode="json"))
        self.store.append(envelope)
        try:
            await self.submit_attestation(attestation.subject_peer_id, envelope)
        except Exception:
            pass

    def backfill_attestations(self) -> None:
        existing_ids = self.existing_attestation_ids()
        primary_by_receipt_id: dict[str, ExecutionReceipt] = {}
        pending: list[Attestation] = []

        for envelope in self.store.envelopes_by_type("execution_receipt"):
            receipt = ExecutionReceipt.model_validate(envelope.payload)
            primary_by_receipt_id[receipt.receipt_id] = receipt
            attestation = self.make_execution_attestation(receipt)
            if attestation.attestation_id not in existing_ids:
                pending.append(attestation)
                existing_ids.add(attestation.attestation_id)

        for envelope in self.store.envelopes_by_type("verification_receipt"):
            receipt = VerificationReceipt.model_validate(envelope.payload)
            primary_receipt = primary_by_receipt_id.get(receipt.primary_receipt_id)
            attestation = self.make_verification_attestation(
                receipt,
                primary_subject_peer_id=primary_receipt.worker_peer_id if primary_receipt else None,
            )
            if attestation.attestation_id not in existing_ids:
                pending.append(attestation)
                existing_ids.add(attestation.attestation_id)

        for attestation in pending:
            self.store.append(self.sign_event("attestation", attestation.model_dump(mode="json")))

    def payment_mode(self) -> str:
        currency = self.settings.settlement_currency or "0G"
        if self.settings.daemon_enable_worker and self.settings.worker_payout_wallet:
            return f"requester-settled {currency.lower()}"
        return "payment-disabled demo mode"

    def quote_payment_terms(self, price: float | None) -> PaymentTerms:
        return PaymentTerms(
            quoted_price=price,
            currency=self.settings.settlement_currency or "0G",
            payment_terms=self.payment_mode(),
        )

    def snapshot_payment_terms(
        self,
        *,
        peer_id: str,
        capability_name: CapabilityName,
        discovered_nodes: list[dict[str, Any]],
    ) -> PaymentTerms:
        price: float | None = None
        for node in discovered_nodes:
            if node.get("peer_id") != peer_id:
                continue
            for capability in node.get("capabilities", []):
                if capability.get("name") == capability_name.value:
                    raw_price = capability.get("price_per_invocation")
                    if isinstance(raw_price, (int, float)):
                        price = float(raw_price)
                    break
            break
        if price is None:
            price = self.capability_price(capability_name)
        return self.quote_payment_terms(price)

    def settlement_event_type(self, status: SettlementStatus) -> str:
        if status == SettlementStatus.TRIGGERED:
            return "settlement_triggered"
        if status == SettlementStatus.CONFIRMED:
            return "settlement_confirmed"
        if status == SettlementStatus.FAILED:
            return "settlement_failed"
        return "settlement_requested"

    def store_settlement(self, settlement: SettlementRecord) -> SettlementRecord:
        self.store.append(self.sign_event(self.settlement_event_type(settlement.status), settlement.model_dump(mode="json")))
        return settlement

    def build_execution_settlement(self, receipt: ExecutionReceipt) -> SettlementRecord | None:
        amount = receipt.payment.quoted_price
        if not receipt.result.success or amount is None or amount <= 0:
            return None
        if not receipt.worker_wallet:
            return None
        now = datetime.now(UTC)
        return SettlementRecord(
            settlement_id=f"execution:{receipt.receipt_id}",
            job_id=receipt.job_id,
            receipt_id=receipt.receipt_id,
            worker_peer_id=receipt.worker_peer_id,
            worker_wallet=receipt.worker_wallet,
            role=receipt.role,
            capability_name=receipt.result.task_type,
            amount=float(amount),
            currency=receipt.payment.currency or self.settings.settlement_currency or "0G",
            token_address=self.settings.settlement_token_address,
            network=self.settings.settlement_network,
            status=SettlementStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

    def build_verification_settlement(
        self,
        receipt: VerificationReceipt,
        verification_request: VerificationRequest,
    ) -> SettlementRecord | None:
        amount = verification_request.execution_request.payment.quoted_price
        if not receipt.result.success or amount is None or amount <= 0:
            return None
        if not receipt.verifier_wallet:
            return None
        now = datetime.now(UTC)
        return SettlementRecord(
            settlement_id=f"verification:{receipt.receipt_id}",
            job_id=receipt.result.job_id,
            receipt_id=receipt.receipt_id,
            worker_peer_id=receipt.verifier_peer_id,
            worker_wallet=receipt.verifier_wallet,
            role=verification_request.execution_request.role,
            capability_name=receipt.result.task_type,
            amount=float(amount),
            currency=verification_request.execution_request.payment.currency or self.settings.settlement_currency or "0G",
            token_address=self.settings.settlement_token_address,
            network=self.settings.settlement_network,
            status=SettlementStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

    async def request_settlement(self, settlement: SettlementRecord | None) -> SettlementRecord | None:
        if settlement is None:
            return None
        existing = self.store.settlement_by_receipt(settlement.receipt_id)
        if existing is not None:
            return SettlementRecord.model_validate(existing)
        self.store_settlement(settlement)
        return settlement

    async def _rpc_call(self, rpc_url: str, method: str, params: list[Any]) -> Any:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                rpc_url,
                json={"jsonrpc": "2.0", "id": str(uuid4()), "method": method, "params": params},
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        return payload.get("result")

    def _payment_signer_key(self) -> str | None:
        key = (self.settings.settlement_payment_private_key or "").strip()
        if key:
            return key
        path = (self.settings.settlement_payment_private_key_path or "").strip()
        if path:
            try:
                return Path(path).read_text(encoding="utf-8").strip() or None
            except OSError:
                return None
        if self.identity is not None:
            return self.identity.private_key_hex
        return None

    async def _broadcast_native_payment(
        self, settlement: SettlementRecord
    ) -> tuple[str | None, str | None]:
        rpc_url = (self.settings.settlement_rpc_url or "").strip()
        if not rpc_url:
            return None, "no settlement_rpc_url configured"
        if settlement.network != self.settings.settlement_network:
            return None, f"settlement network {settlement.network} not supported"
        worker_wallet = settlement.worker_wallet
        if not (worker_wallet.lower().startswith("0x") and len(worker_wallet) == 42):
            return None, f"invalid worker wallet: {worker_wallet}"
        try:
            amount_wei = int(
                (Decimal(str(settlement.amount)) * (Decimal(10) ** NATIVE_TOKEN_DECIMALS)).to_integral_value()
            )
        except (InvalidOperation, ValueError) as exc:
            return None, f"invalid amount: {exc}"
        if amount_wei <= 0:
            return None, "amount is zero"
        signer_key = self._payment_signer_key()
        if not signer_key:
            return None, "no payment private key configured"
        try:
            account = Account.from_key(signer_key)
        except Exception as exc:
            return None, f"invalid payment key: {exc}"
        try:
            nonce_hex = await self._rpc_call(
                rpc_url, "eth_getTransactionCount", [account.address, "pending"]
            )
            gas_price_hex = await self._rpc_call(rpc_url, "eth_gasPrice", [])
        except Exception as exc:
            return None, f"rpc preflight failed: {exc}"
        try:
            tx = {
                "to": worker_wallet,
                "value": amount_wei,
                "gas": NATIVE_TRANSFER_GAS,
                "gasPrice": int(gas_price_hex, 16),
                "nonce": int(nonce_hex, 16),
                "chainId": int(self.settings.settlement_chain_id),
            }
            signed = account.sign_transaction(tx)
        except Exception as exc:
            return None, f"sign failed: {exc}"
        raw_bytes = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
        if raw_bytes is None:
            return None, "signed tx missing raw payload"
        raw_hex = "0x" + bytes(raw_bytes).hex()
        try:
            tx_hash = await self._rpc_call(rpc_url, "eth_sendRawTransaction", [raw_hex])
        except Exception as exc:
            return None, f"send failed: {exc}"
        if not isinstance(tx_hash, str):
            return None, "rpc did not return a tx hash"
        logger.info(
            "settlement broadcast tx=%s amount=%s %s to=%s",
            tx_hash,
            settlement.amount,
            settlement.currency,
            worker_wallet,
        )
        return tx_hash, None

    async def _check_tx_receipt(self, tx_hash: str) -> tuple[str | None, str | None]:
        """Returns (status, error). status is 'success', 'failed', or None when still pending."""
        rpc_url = (self.settings.settlement_rpc_url or "").strip()
        if not rpc_url:
            return None, None
        try:
            receipt = await self._rpc_call(rpc_url, "eth_getTransactionReceipt", [tx_hash])
        except Exception as exc:
            return None, f"receipt poll failed: {exc}"
        if receipt is None:
            return None, None
        status_hex = receipt.get("status")
        if status_hex is None:
            return None, None
        try:
            status_int = int(status_hex, 16)
        except (TypeError, ValueError):
            return None, None
        if status_int == 1:
            return "success", None
        return "failed", "transaction reverted"

    async def reconcile_pending_settlements(self) -> None:
        verification_requests: dict[str, VerificationRequest] = {}
        for envelope in self.store.envelopes_by_type("request_verification"):
            request = VerificationRequest.model_validate(envelope.payload)
            verification_requests[request.verification_id] = request

        for envelope in self.store.envelopes_by_type("execution_receipt"):
            receipt = ExecutionReceipt.model_validate(envelope.payload)
            if self.store.settlement_by_receipt(receipt.receipt_id) is None:
                settlement = self.build_execution_settlement(receipt)
                await self.request_settlement(settlement)

        for envelope in self.store.envelopes_by_type("verification_receipt"):
            receipt = VerificationReceipt.model_validate(envelope.payload)
            if self.store.settlement_by_receipt(receipt.receipt_id) is not None:
                continue
            request = verification_requests.get(receipt.verification_id)
            if request is None:
                continue
            settlement = self.build_verification_settlement(receipt, request)
            await self.request_settlement(settlement)

        for raw in self.store.settlements():
            settlement = SettlementRecord.model_validate(raw)
            if settlement.status in {SettlementStatus.CONFIRMED, SettlementStatus.FAILED}:
                continue
            if settlement.status == SettlementStatus.PENDING:
                tx_hash, err = await self._broadcast_native_payment(settlement)
                if tx_hash:
                    self.store_settlement(
                        settlement.model_copy(
                            update={
                                "status": SettlementStatus.TRIGGERED,
                                "tx_hash": tx_hash,
                                "failure_reason": None,
                                "updated_at": datetime.now(UTC),
                            }
                        )
                    )
                else:
                    self.store_settlement(
                        settlement.model_copy(
                            update={
                                "status": SettlementStatus.FAILED,
                                "failure_reason": err or "broadcast failed",
                                "updated_at": datetime.now(UTC),
                            }
                        )
                    )
                continue
            if settlement.status == SettlementStatus.TRIGGERED and settlement.tx_hash:
                outcome, err = await self._check_tx_receipt(settlement.tx_hash)
                if outcome == "success":
                    self.store_settlement(
                        settlement.model_copy(
                            update={
                                "status": SettlementStatus.CONFIRMED,
                                "failure_reason": None,
                                "updated_at": datetime.now(UTC),
                            }
                        )
                    )
                elif outcome == "failed":
                    self.store_settlement(
                        settlement.model_copy(
                            update={
                                "status": SettlementStatus.FAILED,
                                "failure_reason": err or "transaction reverted",
                                "updated_at": datetime.now(UTC),
                            }
                        )
                    )

    async def live_nodes(self) -> list[dict[str, Any]]:
        # A live node = signed advertisement with active=true and TTL not expired.
        # store.known_nodes() already enforces both (active && expires_at >= now)
        # in its `active` field. The discovery loop refreshes ads every ~45s, so
        # this is a sufficient liveness signal without an extra per-request
        # agent-card probe over the AXL mesh (which used to flicker nodes in
        # and out of the dashboard when AXL routing was momentarily slow).
        live = [node for node in self.store.known_nodes() if node.get("active")]
        live.sort(key=lambda item: (item["region"], item["label"]))
        return live

    async def send_coordination_request(
        self,
        peer_id: str,
        method: str,
        params: dict[str, Any],
        *,
        fallback_tool_name: str | None = None,
    ) -> dict[str, Any]:
        a2a_request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": str(uuid4()),
            "params": params,
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(f"{self.settings.axl_node_url}/a2a/{peer_id}", json=a2a_request)
                response.raise_for_status()
                payload = response.json()

            if payload.get("error"):
                error = payload["error"]
                message = error.get("message") if isinstance(error, dict) else str(error)
                raise HTTPException(status_code=502, detail=message)
            logger.info("coordination transport=a2a method=%s peer=%s", method, peer_id)
            return payload.get("result", {})
        except Exception as exc:
            if fallback_tool_name is None:
                raise
            logger.warning("coordination transport=mcp-fallback method=%s peer=%s reason=%s", method, peer_id, str(exc))
            return await self.post_nodehub_tool(peer_id, fallback_tool_name, params)

    async def post_nodehub_tool(self, peer_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self.post_mcp_service(
            peer_id,
            self.settings.nodehub_service_name,
            {
                "jsonrpc": "2.0",
                "id": str(uuid4()),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )

    async def post_mcp_service(self, peer_id: str, service_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.settings.axl_node_url}/mcp/{peer_id}/{service_name}",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        return body.get("result", {}).get("structuredContent", {})

    async def post_mcp(self, peer_id: str, task_type: CapabilityName, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "tools/call",
            "params": {"name": task_type.value, "arguments": arguments},
        }
        return await self.post_mcp_service(peer_id, self.settings.worker_service_name, payload)

    async def send_execution_request(self, peer_id: str, envelope: SignedEnvelope) -> dict[str, Any]:
        return await self.send_demo_request(
            peer_id,
            kind="execution_request",
            payload={"envelope": envelope.model_dump(mode="json")},
        )

    async def publish_advertisement(self, peer_id: str, envelope: SignedEnvelope) -> None:
        await self.send_raw_message(
            peer_id,
            DemoTransportMessage(
                kind="node_advertisement",
                payload={"envelope": envelope.model_dump(mode="json")},
            ),
        )

    async def store_and_relay_advertisement(self, envelope: SignedEnvelope, *, from_peer_id: str | None = None) -> bool:
        self.verify_envelope(envelope)
        is_new = not self.store.has_event(envelope.event_id)
        self.store.append(envelope)
        try:
            ad = NodeAdvertisement.model_validate(envelope.payload)
            logger.info(
                "store ad signer=%s region=%s caps=%s new=%s",
                envelope.signer_peer_id[:12],
                ad.region,
                [cap.name.value for cap in ad.capabilities],
                is_new,
            )
        except Exception:
            pass
        if not is_new:
            return False

        relay_peer_ids = [
            peer_id
            for peer_id in await self.seed_peer_ids([])
            if peer_id not in {self.peer_id, envelope.signer_peer_id, from_peer_id}
        ]
        for peer_id in relay_peer_ids:
            try:
                await self.publish_advertisement(peer_id, envelope)
            except Exception:
                continue
        return True

    async def announce_current_advertisement(self) -> None:
        envelope = self.current_advertisement_envelope()
        self.store.append(envelope)
        peer_ids = await self.seed_peer_ids([])
        logger.info(
            "announce: pushing own advertisement to %d peer(s): %s",
            len(peer_ids),
            [pid[:12] for pid in peer_ids],
        )
        for peer_id in peer_ids:
            try:
                await self.publish_advertisement(peer_id, envelope)
            except Exception:
                continue

    async def send_verification_request(self, peer_id: str, envelope: SignedEnvelope) -> dict[str, Any]:
        return await self.send_demo_request(
            peer_id,
            kind="verification_request",
            payload={"envelope": envelope.model_dump(mode="json")},
        )

    async def submit_attestation(self, peer_id: str, envelope: SignedEnvelope) -> dict[str, Any]:
        return await self.send_demo_request(
            peer_id,
            kind="attestation",
            payload={"envelope": envelope.model_dump(mode="json")},
        )

    async def fetch_remote_receipt(self, peer_id: str, receipt_id: str) -> dict[str, Any]:
        return await self.send_demo_request(
            peer_id,
            kind="fetch_receipt_request",
            payload={"receipt_id": receipt_id},
        )

    async def execute_local_task(
        self,
        *,
        task_type: CapabilityName,
        arguments: dict[str, Any],
        job_id: str,
        reservation_id: str,
    ) -> TaskResult:
        if not self.settings.daemon_enable_worker:
            raise HTTPException(status_code=400, detail="worker execution is disabled on this daemon")
        if task_type == CapabilityName.DIAGNOSE_FAILURE:
            diagnosis = await self.diagnose_failure(arguments)
            return TaskResult(
                job_id=job_id,
                reservation_id=reservation_id,
                task_type=task_type,
                node_peer_id=self.peer_id,
                node_region=self.settings.worker_region,
                success=True,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                raw={},
                diagnosis=diagnosis["diagnosis"],
                confidence=diagnosis["confidence"],
            )
        if task_type not in self.enabled_worker_capability_names():
            raise HTTPException(status_code=403, detail=f"{task_type.value} is disabled on this worker")
        plugin = self.registry.get(task_type)
        result = await plugin.execute(
            arguments,
            job_id=job_id,
            reservation_id=reservation_id,
            node_peer_id=self.peer_id,
            node_region=self.settings.worker_region,
        )
        if result.success:
            return result
        diagnosis_summary = await self.generate_diagnosis(
            task_type=task_type,
            job_id=job_id,
            reservation_id=reservation_id,
            original_inputs=arguments,
            failure=result.failure,
        )
        result.diagnosis = diagnosis_summary.diagnosis
        result.confidence = diagnosis_summary.confidence
        result.raw["diagnosis_summary"] = {
            "suggested_next_step": diagnosis_summary.suggested_next_step,
            "follow_up_summary": diagnosis_summary.follow_up_summary,
            "source": diagnosis_summary.source,
        }
        return result

    async def _execute_plugin_only(
        self,
        *,
        task_type: CapabilityName,
        arguments: dict[str, Any],
        job_id: str,
        reservation_id: str,
    ) -> TaskResult:
        plugin = self.registry.get(task_type)
        return await plugin.execute(
            arguments,
            job_id=job_id,
            reservation_id=reservation_id,
            node_peer_id=self.peer_id,
            node_region=self.settings.worker_region,
        )

    async def generate_diagnosis(
        self,
        *,
        task_type: CapabilityName,
        job_id: str,
        reservation_id: str,
        original_inputs: dict[str, Any],
        failure: StructuredFailure | None,
    ) -> DiagnosisSummary:
        summary = await self.diagnoser.diagnose(
            task_type=task_type,
            job_id=job_id,
            reservation_id=reservation_id,
            node_peer_id=self.peer_id,
            node_region=self.settings.worker_region,
            original_inputs=original_inputs,
            failure=failure,
            follow_up_runner=lambda capability, follow_up_args: self._execute_plugin_only(
                task_type=capability,
                arguments=follow_up_args,
                job_id=job_id,
                reservation_id=reservation_id,
            ),
        )
        summary = summary.model_copy(
            update={
                "job_id": job_id,
                "reservation_id": reservation_id,
                "node_peer_id": self.peer_id,
                "node_region": self.settings.worker_region,
            }
        )
        self.store.append(self.sign_event("diagnosis_generated", summary.model_dump(mode="json")))
        return summary

    async def diagnose_failure(self, arguments: dict[str, Any]) -> dict[str, Any]:
        task_type = CapabilityName(arguments.get("task_type", CapabilityName.HTTP_CHECK.value))
        failure_payload = arguments.get("failure") or arguments.get("previous_result", {}).get("failure")
        failure = StructuredFailure.model_validate(failure_payload) if failure_payload else None
        summary = await self.generate_diagnosis(
            task_type=task_type,
            job_id=arguments.get("job_id", "adhoc"),
            reservation_id=arguments.get("reservation_id", "adhoc"),
            original_inputs=arguments,
            failure=failure,
        )
        return {
            "diagnosis": summary.diagnosis,
            "confidence": summary.confidence,
            "suggested_next_step": summary.suggested_next_step,
            "follow_up_summary": summary.follow_up_summary,
            "follow_up_results": summary.follow_up_results,
            "source": summary.source,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    def mirror_diagnosis_event(self, result: TaskResult) -> DiagnosisSummary | None:
        if not result.diagnosis:
            return None
        summary = DiagnosisSummary(
            job_id=result.job_id,
            reservation_id=result.reservation_id,
            task_type=result.task_type,
            node_peer_id=result.node_peer_id,
            node_region=result.node_region,
            diagnosis=result.diagnosis,
            confidence=result.confidence or 0.0,
            suggested_next_step=result.raw.get("diagnosis_summary", {}).get("suggested_next_step"),
            follow_up_summary=result.raw.get("diagnosis_summary", {}).get("follow_up_summary"),
            follow_up_results={},
            source=result.raw.get("diagnosis_summary", {}).get("source", "deterministic"),
        )
        self.store.append(self.sign_event("diagnosis_generated", summary.model_dump(mode="json")))
        return summary

    async def handle_quote_request(self, envelope: SignedEnvelope) -> SignedEnvelope:
        self.verify_envelope(envelope)
        self.store.append(envelope)
        request = QuoteRequest.model_validate(envelope.payload)
        capability = next((cap for cap in self.worker_capabilities() if cap.name == request.capability_name), None)
        if capability is None:
            raise HTTPException(status_code=404, detail="capability unavailable")
        offer = QuoteOffer(
            quote_id=str(uuid4()),
            request_id=request.request_id,
            worker_wallet=self.public_wallet_address(),
            worker_peer_id=self.peer_id,
            capability_name=request.capability_name,
            region=self.settings.worker_region,
            country_code=self.settings.worker_country_code,
            available_capacity=self.settings.worker_capacity,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            payment=self.quote_payment_terms(capability.price_per_invocation),
            metadata={"label": self.settings.worker_public_label},
        )
        signed = self.sign_event("quote_response", offer.model_dump(mode="json"))
        self.store.append(signed)
        return signed

    async def handle_execution_request(self, envelope: SignedEnvelope, verification: bool = False) -> SignedEnvelope:
        self.verify_envelope(envelope)
        self.store.append(envelope)
        if verification:
            request = VerificationRequest.model_validate(envelope.payload)
            inner = request.execution_request
            task_result = await self.execute_local_task(
                task_type=inner.task_type,
                arguments=inner.inputs,
                job_id=inner.job_id,
                reservation_id=request.verification_id,
            )
            receipt = VerificationReceipt(
                receipt_id=str(uuid4()),
                verification_id=request.verification_id,
                primary_receipt_id=request.primary_receipt_id,
                verifier_wallet=self.public_wallet_address(),
                verifier_peer_id=self.peer_id,
                result=task_result,
                status=VerificationStatus.VERIFIED if task_result.success else VerificationStatus.MISMATCH,
                notes="Verifier execution recorded." if task_result.success else "Verifier execution failed.",
            )
            signed = self.sign_event("verification_receipt", receipt.model_dump(mode="json"))
            self.store.append(signed)
            return signed

        request = ExecutionRequest.model_validate(envelope.payload)
        task_result = await self.execute_local_task(
            task_type=request.task_type,
            arguments=request.inputs,
            job_id=request.job_id,
            reservation_id=request.reservation_id,
        )
        receipt = ExecutionReceipt(
            receipt_id=str(uuid4()),
            job_id=request.job_id,
            lease_id=request.lease_id,
            quote_id=request.quote_id,
            requester_wallet=request.requester_wallet,
            requester_peer_id=request.requester_peer_id,
            worker_wallet=self.public_wallet_address(),
            worker_peer_id=self.peer_id,
            role=request.role,
            result=task_result,
            payment=request.payment,
        )
        signed = self.sign_event("execution_receipt", receipt.model_dump(mode="json"))
        self.store.append(signed)
        return signed

    async def handle_attestation(self, envelope: SignedEnvelope) -> SignedEnvelope:
        self.verify_envelope(envelope)
        self.store.append(envelope)
        return self.sign_event("attestation_ack", {"attestation_id": envelope.payload.get("attestation_id"), "acknowledged_at": datetime.now(UTC).isoformat()})

    async def handle_nodehub_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "describe_node":
            return self.current_advertisement().model_dump(mode="json")
        if tool_name == "discover_nodes":
            self.store.append(self.current_advertisement_envelope())
            depth = int(arguments.get("depth", 0) or 0)
            if depth > 0:
                peer_ids = await self.seed_peer_ids([])
                for peer_id in peer_ids:
                    try:
                        result = await self.send_coordination_request(
                            peer_id,
                            "discover_nodes",
                            {"depth": depth - 1},
                            fallback_tool_name="discover_nodes",
                        )
                        envelopes = [SignedEnvelope.model_validate(item) for item in result.get("envelopes", [])]
                        for envelope in envelopes:
                            self.verify_envelope(envelope)
                        self.store.import_many(envelopes)
                    except Exception:
                        continue
            envelopes = [envelope.model_dump(mode="json") for envelope in self.store.latest_node_advertisement_envelopes()]
            return {"envelopes": envelopes}
        if tool_name == "advertise_node":
            envelope = SignedEnvelope.model_validate(arguments["envelope"])
            stored = await self.store_and_relay_advertisement(envelope)
            return {"stored": stored}
        if tool_name == "request_quote":
            envelope = SignedEnvelope.model_validate(arguments["envelope"])
            result = await self.handle_quote_request(envelope)
            return {"envelope": result.model_dump(mode="json")}
        if tool_name == "request_job_execution":
            envelope = SignedEnvelope.model_validate(arguments["envelope"])
            result = await self.handle_execution_request(envelope, verification=False)
            return {"envelope": result.model_dump(mode="json")}
        if tool_name == "request_verification":
            envelope = SignedEnvelope.model_validate(arguments["envelope"])
            result = await self.handle_execution_request(envelope, verification=True)
            return {"envelope": result.model_dump(mode="json")}
        if tool_name == "submit_attestation":
            envelope = SignedEnvelope.model_validate(arguments["envelope"])
            result = await self.handle_attestation(envelope)
            return {"envelope": result.model_dump(mode="json")}
        if tool_name == "fetch_receipt":
            return self.store.receipt_by_id(arguments["receipt_id"]) or {}
        raise HTTPException(status_code=404, detail=f"unknown nodehub tool: {tool_name}")

    def agent_card(self) -> dict[str, Any]:
        return {
            "name": "NodeHub Daemon",
            "description": "Decentralized NodeHub coordination daemon over AXL.",
            "url": f"http://{self.settings.daemon_host}:{self.settings.daemon_port}",
            "skills": [
                {"id": "describe_node", "name": "describe_node"},
                {"id": self.settings.nodehub_service_name, "name": "nodehub"},
                {"id": self.settings.worker_service_name, "name": "webops-worker"},
                {"id": "discover_nodes", "name": "discover_nodes"},
                {"id": "advertise_node", "name": "advertise_node"},
                {"id": "request_quote", "name": "request_quote"},
                {"id": "request_job_execution", "name": "request_job_execution"},
                {"id": "request_verification", "name": "request_verification"},
                {"id": "submit_attestation", "name": "submit_attestation"},
                {"id": "fetch_receipt", "name": "fetch_receipt"},
            ],
        }

    async def discover_remote_nodes(self, explicit_peers: list[str] | None = None, depth: int = 1) -> list[dict[str, Any]]:
        self.store.append(self.current_advertisement_envelope())
        peer_ids = await self.seed_peer_ids(explicit_peers or [])
        logger.info(
            "discover: sending discover_request to %d peer(s): %s",
            len(peer_ids),
            [pid[:12] for pid in peer_ids],
        )
        for peer_id in peer_ids:
            try:
                await self.send_raw_message(
                    peer_id,
                    DemoTransportMessage(
                        kind="discover_request",
                        reply_to_peer_id=self.peer_id,
                        payload={"depth": max(depth, 1)},
                    ),
                )
            except Exception:
                continue
        await asyncio.sleep(1.0 + max(depth - 1, 0) * 0.5)
        return await self.live_nodes()

    async def seed_peer_ids(self, explicit_peers: list[str]) -> list[str]:
        topology = await self.get_topology()
        peer_ids = list(
            dict.fromkeys(
                explicit_peers
                + self.settings.daemon_peer_seeds
                + self._topology_peer_ids(topology)
            )
        )
        return [peer_id for peer_id in peer_ids if peer_id and peer_id != self.peer_id]

    @staticmethod
    def _topology_peer_ids(topology: dict[str, Any]) -> list[str]:
        items: list[str] = []
        our_public_key = topology.get("our_public_key")

        for peer in topology.get("peers") or []:
            if isinstance(peer, dict):
                value = peer.get("public_key") or peer.get("peer_id") or peer.get("key")
                if value:
                    items.append(value)
            elif isinstance(peer, str):
                items.append(peer)

        for peer in topology.get("tree") or []:
            if not isinstance(peer, dict):
                continue
            value = peer.get("public_key") or peer.get("peer_id") or peer.get("key")
            parent = peer.get("parent")
            if value and value != our_public_key:
                items.append(value)
            if parent and parent != our_public_key:
                items.append(parent)

        deduped: list[str] = []
        for item in items:
            if item and item not in deduped:
                deduped.append(item)
        return deduped

    async def request_job(self, payload: JobRequestPayload) -> dict[str, Any]:
        job_id = str(uuid4())
        discovered_nodes = await self.discover_remote_nodes([])
        requested_region = payload.regions[0].lower() if payload.regions else None
        selected_workers = [
            node
            for node in discovered_nodes
            if any(cap.get("name") == payload.task_type.value for cap in node.get("capabilities", []))
            and (requested_region is None or str(node.get("region", "")).lower() == requested_region)
        ]
        if not selected_workers:
            region_label = requested_region or "the selected region"
            raise HTTPException(status_code=404, detail=f"No live {payload.task_type.value} worker found for {region_label}.")

        selected_worker = selected_workers[0]
        plan = JobPlan(
            job_id=job_id,
            task_type=payload.task_type,
            requested_regions=[requested_region] if requested_region else [],
            selected_primary_peer_ids=[selected_worker["peer_id"]],
            selected_verifier_peer_ids=[],
            use_lease_backed=False,
            selected_lease_id=None,
            rationale=f"Direct demo dispatch to {selected_worker['label']} in {selected_worker['region']}.",
            verification_requested=False,
            planner_mode="deterministic",
        )

        self.store.append(self.sign_event("job_plan", plan.model_dump(mode="json")))

        receipt_ids: list[str] = []
        primary_receipts: list[ExecutionReceipt] = []
        primary_request: ExecutionRequest | None = None
        mirrored_diagnoses: list[DiagnosisSummary] = []
        for worker_peer_id in plan.selected_primary_peer_ids:
            reservation_id = str(uuid4())
            request = ExecutionRequest(
                job_id=job_id,
                reservation_id=reservation_id,
                lease_id=None,
                requester_wallet=self.assert_identity().wallet_address,
                requester_peer_id=self.peer_id,
                worker_peer_id=worker_peer_id,
                task_type=payload.task_type,
                inputs=payload.inputs,
                role=ReservationRole.PRIMARY,
                verification_policy=VerificationPolicy(verifier_count=0),
                payment=self.snapshot_payment_terms(
                    peer_id=worker_peer_id,
                    capability_name=payload.task_type,
                    discovered_nodes=discovered_nodes,
                ),
            )
            if primary_request is None:
                primary_request = request
            envelope = self.sign_event("execution_request", request.model_dump(mode="json"))
            self.store.append(envelope)
            receipt_result = await self.send_execution_request(worker_peer_id, envelope)
            receipt_envelope = SignedEnvelope.model_validate(receipt_result["envelope"])
            self.verify_envelope(receipt_envelope)
            self.store.append(receipt_envelope)
            receipt = ExecutionReceipt.model_validate(receipt_envelope.payload)
            primary_receipts.append(receipt)
            receipt_ids.append(receipt.receipt_id)
            diagnosis_summary = self.mirror_diagnosis_event(receipt.result)
            if diagnosis_summary is not None:
                mirrored_diagnoses.append(diagnosis_summary)
            await self.request_settlement(self.build_execution_settlement(receipt))

        verification_receipts: list[VerificationReceipt] = []

        if primary_request is not None:
            report_summary = await self.reporter.summarize(
                job_id=job_id,
                execution_request=primary_request,
                primary_receipts=primary_receipts,
                verifier_receipts=verification_receipts,
                job_plan=plan,
                diagnoses=mirrored_diagnoses,
            )
            self.store.append(self.sign_event("report_summary_generated", report_summary.model_dump(mode="json")))

        for receipt in primary_receipts:
            await self.store_attestation(self.make_execution_attestation(receipt))

        report = self.store.job_report(job_id)
        if report is None:
            raise HTTPException(status_code=500, detail="job report unavailable")
        return report

    async def handle_a2a(self, payload: JSONRPCRequest) -> dict[str, Any]:
        params = payload.params or {}
        try:
            if payload.method == "discover_nodes":
                self.store.append(self.current_advertisement_envelope())
                depth = int(params.get("depth", 0) or 0)
                if depth > 0:
                    peer_ids = await self.seed_peer_ids([])
                    for peer_id in peer_ids:
                        try:
                            result = await self.send_coordination_request(
                                peer_id,
                                "discover_nodes",
                                {"depth": depth - 1},
                                fallback_tool_name="discover_nodes",
                            )
                            envelopes = [SignedEnvelope.model_validate(item) for item in result.get("envelopes", [])]
                            for envelope in envelopes:
                                self.verify_envelope(envelope)
                            self.store.import_many(envelopes)
                        except Exception:
                            continue
                envelopes = [envelope.model_dump(mode="json") for envelope in self.store.latest_node_advertisement_envelopes()]
                return {"jsonrpc": "2.0", "id": payload.id, "result": {"envelopes": envelopes}}
            if payload.method == "advertise_node":
                envelope = SignedEnvelope.model_validate(params["envelope"])
                stored = await self.store_and_relay_advertisement(envelope)
                return {"jsonrpc": "2.0", "id": payload.id, "result": {"stored": stored}}
            if payload.method == "request_quote":
                envelope = SignedEnvelope.model_validate(params["envelope"])
                result = await self.handle_quote_request(envelope)
                return {"jsonrpc": "2.0", "id": payload.id, "result": {"envelope": result.model_dump(mode="json")}}
            if payload.method == "request_job_execution":
                envelope = SignedEnvelope.model_validate(params["envelope"])
                result = await self.handle_execution_request(envelope, verification=False)
                return {"jsonrpc": "2.0", "id": payload.id, "result": {"envelope": result.model_dump(mode="json")}}
            if payload.method == "request_verification":
                envelope = SignedEnvelope.model_validate(params["envelope"])
                result = await self.handle_execution_request(envelope, verification=True)
                return {"jsonrpc": "2.0", "id": payload.id, "result": {"envelope": result.model_dump(mode="json")}}
            if payload.method == "submit_attestation":
                envelope = SignedEnvelope.model_validate(params["envelope"])
                result = await self.handle_attestation(envelope)
                return {"jsonrpc": "2.0", "id": payload.id, "result": {"envelope": result.model_dump(mode="json")}}
            if payload.method == "fetch_receipt":
                receipt = self.store.receipt_by_id(params["receipt_id"])
                return {"jsonrpc": "2.0", "id": payload.id, "result": receipt or {}}
        except HTTPException as exc:
            return {"jsonrpc": "2.0", "id": payload.id, "error": {"code": exc.status_code, "message": exc.detail}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": payload.id, "error": {"code": -32000, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": payload.id, "error": {"code": -32601, "message": f"Unsupported method: {payload.method}"}}


def create_daemon_app(settings: PlatformSettings | None = None) -> FastAPI:
    # Ensure our INFO-level diagnostics from this module are visible alongside
    # uvicorn's logs. uvicorn's default Python root logger is WARNING; without
    # this, send/recv/discover/announce traces would be silently dropped.
    if not logger.handlers and logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    app = FastAPI(title="NodeHub Daemon", version="0.2.0")
    runtime = DaemonRuntime(settings or get_settings())
    advertise_task: asyncio.Task | None = None
    discovery_task: asyncio.Task | None = None
    settlement_task: asyncio.Task | None = None
    announce_task: asyncio.Task | None = None
    recv_task: asyncio.Task | None = None

    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime.settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def startup() -> None:
        nonlocal advertise_task, discovery_task, settlement_task, announce_task, recv_task
        await runtime.startup()

        async def advertisement_loop() -> None:
            while True:
                runtime.store.append(runtime.current_advertisement_envelope())
                await asyncio.sleep(45)

        async def discovery_loop() -> None:
            await asyncio.sleep(2)
            while True:
                try:
                    await runtime.discover_remote_nodes([], depth=1)
                except Exception:
                    pass
                await asyncio.sleep(45)

        async def announce_loop() -> None:
            await asyncio.sleep(4)
            while True:
                try:
                    await runtime.announce_current_advertisement()
                except Exception:
                    pass
                await asyncio.sleep(45)

        async def settlement_loop() -> None:
            await asyncio.sleep(3)
            while True:
                try:
                    await runtime.reconcile_pending_settlements()
                except Exception:
                    pass
                await asyncio.sleep(max(5, runtime.settings.settlement_reconcile_interval_seconds))

        advertise_task = asyncio.create_task(advertisement_loop())
        discovery_task = asyncio.create_task(discovery_loop())
        announce_task = asyncio.create_task(announce_loop())
        settlement_task = asyncio.create_task(settlement_loop())
        recv_task = asyncio.create_task(runtime.recv_loop())

    @app.on_event("shutdown")
    async def shutdown() -> None:
        if advertise_task is not None:
            advertise_task.cancel()
        if discovery_task is not None:
            discovery_task.cancel()
        if announce_task is not None:
            announce_task.cancel()
        if settlement_task is not None:
            settlement_task.cancel()
        if recv_task is not None:
            recv_task.cancel()
        if runtime.settings.daemon_enable_worker:
            await runtime.deregister_from_router()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "daemon"}

    @app.get("/identity")
    async def identity() -> dict[str, Any]:
        identity = runtime.assert_identity()
        return {
            "wallet_address": runtime.public_wallet_address(),
            "payout_wallet_address": runtime.public_wallet_address(),
            "signing_wallet_address": identity.wallet_address,
            "peer_id": identity.peer_id,
            "label": runtime.settings.worker_public_label,
            "region": runtime.settings.worker_region,
            "country_code": runtime.settings.worker_country_code,
            "worker_enabled": runtime.settings.daemon_enable_worker,
            "payment_mode": runtime.payment_mode(),
            "enabled_capabilities": [
                cap.name.value
                for cap in runtime.worker_capabilities()
                if cap.name not in {CapabilityName.DESCRIBE_NODE, CapabilityName.DIAGNOSE_FAILURE}
            ],
        }

    @app.get("/nodes")
    async def list_nodes() -> list[dict[str, Any]]:
        runtime.store.append(runtime.current_advertisement_envelope())
        return await runtime.live_nodes()

    @app.post("/discover")
    async def discover(payload: DiscoverRequest) -> list[dict[str, Any]]:
        return await runtime.discover_remote_nodes(payload.peer_ids, depth=payload.depth)

    @app.get("/jobs")
    async def list_jobs() -> list[dict[str, Any]]:
        return runtime.store.jobs()

    @app.post("/jobs/request")
    async def request_job(payload: JobRequestPayload) -> dict[str, Any]:
        return await runtime.request_job(payload)

    @app.get("/jobs/{job_id}/report")
    async def get_job_report(job_id: str) -> dict[str, Any]:
        report = runtime.store.job_report(job_id)
        if report is None:
            raise HTTPException(status_code=404, detail="job not found")
        return report

    @app.get("/reports/jobs/{job_id}")
    async def get_job_report_alias(job_id: str) -> dict[str, Any]:
        report = runtime.store.job_report(job_id)
        if report is None:
            raise HTTPException(status_code=404, detail="job not found")
        return report

    @app.get("/receipts/{receipt_id}")
    async def get_receipt(receipt_id: str) -> dict[str, Any]:
        receipt = runtime.store.receipt_by_id(receipt_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail="receipt not found")
        return receipt

    @app.get("/attestations")
    async def list_attestations() -> list[dict[str, Any]]:
        return runtime.store.attestations()

    @app.get("/settlements")
    async def list_settlements() -> list[dict[str, Any]]:
        return runtime.store.settlements()

    @app.post("/attestations/import")
    async def import_attestations(payload: ImportAttestationsPayload) -> dict[str, Any]:
        for envelope in payload.envelopes:
            runtime.verify_envelope(envelope)
        runtime.store.import_many(payload.envelopes)
        return {"imported": len(payload.envelopes)}

    @app.get("/.well-known/agent-card.json")
    async def agent_card() -> dict[str, Any]:
        return runtime.agent_card()

    @app.post("/")
    async def handle_a2a(payload: JSONRPCRequest) -> dict[str, Any]:
        return await runtime.handle_a2a(payload)

    @app.post("/mcp")
    async def handle_mcp(request: Request, payload: JSONRPCRequest) -> dict[str, Any]:
        requested_service = request.headers.get("X-Service", runtime.settings.worker_service_name)
        nodehub_tools = [
            {"name": "describe_node", "description": "Describe this NodeHub peer and its advertised capabilities."},
            {"name": "discover_nodes", "description": "Return signed node advertisements and related discovery envelopes."},
            {"name": "request_quote", "description": "Submit a signed quote request envelope and return a signed quote response."},
            {"name": "request_job_execution", "description": "Submit a signed execution request and return a signed execution receipt."},
            {"name": "request_verification", "description": "Submit a signed verification request and return a signed verification receipt."},
            {"name": "submit_attestation", "description": "Submit a signed attestation envelope and return an acknowledgement envelope."},
            {"name": "fetch_receipt", "description": "Fetch a previously stored receipt by ID."},
        ]
        worker_tools = [
            {
                "name": capability.name.value,
                "description": capability.description,
            }
            for capability in runtime.worker_capabilities()
        ]

        if payload.method == "tools/list":
            tools = nodehub_tools if requested_service == runtime.settings.nodehub_service_name else worker_tools
            return {
                "jsonrpc": "2.0",
                "id": payload.id,
                "result": {
                    "tools": [
                        {
                            "name": tool["name"],
                            "description": tool["description"],
                            "inputSchema": {"type": "object"},
                        }
                        for tool in tools
                    ]
                },
            }
        if payload.method == "tools/call":
            params = payload.params or {}
            tool_name = params.get("name")
            if not tool_name:
                raise HTTPException(status_code=400, detail="missing tool name")

            if requested_service == runtime.settings.nodehub_service_name:
                result = await runtime.handle_nodehub_tool_call(tool_name, params.get("arguments", {}))
            else:
                if tool_name == CapabilityName.DESCRIBE_NODE.value:
                    result = runtime.current_advertisement().model_dump(mode="json")
                elif tool_name == CapabilityName.DIAGNOSE_FAILURE.value:
                    result = await runtime.diagnose_failure(params.get("arguments", {}))
                else:
                    task_result = await runtime.execute_local_task(
                        task_type=CapabilityName(tool_name),
                        arguments=params.get("arguments", {}),
                        job_id=params.get("arguments", {}).get("job_id", "adhoc"),
                        reservation_id=params.get("arguments", {}).get("reservation_id", "adhoc"),
                    )
                    result = task_result.model_dump(mode="json")
            return {
                "jsonrpc": "2.0",
                "id": payload.id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result)}],
                    "structuredContent": result,
                },
            }
        if payload.method == "initialize":
            server_name = requested_service if requested_service in {runtime.settings.nodehub_service_name, runtime.settings.worker_service_name} else runtime.settings.worker_service_name
            return {
                "jsonrpc": "2.0",
                "id": payload.id,
                "result": {
                    "serverInfo": {"name": server_name, "version": "0.2.0"},
                    "capabilities": {"tools": {}},
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": payload.id,
            "error": {"code": -32601, "message": f"Unsupported method: {payload.method}"},
        }

    return app
