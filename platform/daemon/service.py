from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
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
    LeaseAcceptance,
    LeaseProposal,
    LeaseRelease,
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
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
RPC_URLS = {
    "base-sepolia": "https://sepolia.base.org",
    "sepolia": "https://ethereum-sepolia-rpc.publicnode.com",
}


class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    id: str | int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class DiscoverRequest(BaseModel):
    peer_ids: list[str] = Field(default_factory=list)


class LeaseRequestPayload(BaseModel):
    capability_name: CapabilityName = CapabilityName.HTTP_CHECK
    regions: list[str] = Field(default_factory=list)
    duration_hours: int = 1
    verifier_count: int = 0


class JobRequestPayload(BaseModel):
    task_type: CapabilityName
    inputs: dict[str, Any]
    regions: list[str] = Field(default_factory=list)
    lease_id: str | None = None
    verifier_count: int = 1


class ImportAttestationsPayload(BaseModel):
    envelopes: list[SignedEnvelope]


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
        self._token_decimals_cache: dict[tuple[str, str], int] = {}
        self._block_timestamp_cache: dict[tuple[str, int], datetime] = {}

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

    async def register_with_router(self) -> None:
        endpoint = f"http://{self.settings.daemon_host}:{self.settings.daemon_port}/mcp"
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{self.settings.router_url}/register",
                json={"service": self.settings.nodehub_service_name, "endpoint": endpoint},
            )
            await client.post(
                f"{self.settings.router_url}/register",
                json={"service": self.settings.worker_service_name, "endpoint": endpoint},
            )

    async def deregister_from_router(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for service_name in (self.settings.nodehub_service_name, self.settings.worker_service_name):
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
        capabilities = [
            NodeCapability(name=CapabilityName.DESCRIBE_NODE, description="Describe this node.", price_per_invocation=0.0),
        ]
        capabilities.extend(
            NodeCapability(
                name=plugin.name,
                description=plugin.description,
                price_per_invocation=self.capability_price(plugin.name),
            )
            for plugin in self.registry.all()
            if plugin.name in enabled_capabilities
        )
        capabilities.append(
            NodeCapability(name=CapabilityName.DIAGNOSE_FAILURE, description="Run bounded failure diagnosis.", price_per_invocation=0.0)
        )
        return capabilities

    def capability_price(self, capability_name: CapabilityName) -> float:
        if capability_name == CapabilityName.HTTP_CHECK:
            return self.settings.worker_price_http_check
        if capability_name == CapabilityName.DNS_CHECK:
            return self.settings.worker_price_dns_check
        if capability_name == CapabilityName.LATENCY_PROBE:
            return self.settings.worker_price_latency_probe
        if capability_name == CapabilityName.PING_CHECK:
            return self.settings.worker_price_ping_check
        if capability_name == CapabilityName.API_CALL:
            return self.settings.worker_price_api_call
        if capability_name == CapabilityName.CDN_CHECK:
            return self.settings.worker_price_cdn_check
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
        if self.settings.daemon_enable_worker and self.settings.worker_payout_wallet:
            return "requester-settled usdc"
        return "payment-disabled demo mode"

    def quote_payment_terms(self, price: float | None) -> PaymentTerms:
        return PaymentTerms(
            quoted_price=price,
            currency="USDC",
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
            currency=receipt.payment.currency or "USDC",
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
            currency=verification_request.execution_request.payment.currency or "USDC",
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

    @staticmethod
    def _address_topic(address: str) -> str:
        return "0x" + ("0" * 24) + address.lower().removeprefix("0x")

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

    async def _token_decimals(self, rpc_url: str, token_address: str) -> int:
        cache_key = (rpc_url, token_address.lower())
        if cache_key in self._token_decimals_cache:
            return self._token_decimals_cache[cache_key]
        result = await self._rpc_call(
            rpc_url,
            "eth_call",
            [{"to": token_address, "data": "0x313ce567"}, "latest"],
        )
        decimals = int(result, 16)
        self._token_decimals_cache[cache_key] = decimals
        return decimals

    async def _block_timestamp(self, rpc_url: str, block_number: int) -> datetime:
        cache_key = (rpc_url, block_number)
        if cache_key in self._block_timestamp_cache:
            return self._block_timestamp_cache[cache_key]
        result = await self._rpc_call(rpc_url, "eth_getBlockByNumber", [hex(block_number), False])
        timestamp = datetime.fromtimestamp(int(result["timestamp"], 16), UTC)
        self._block_timestamp_cache[cache_key] = timestamp
        return timestamp

    async def _candidate_token_transfers(self, settlement: SettlementRecord) -> list[dict[str, Any]]:
        rpc_url = RPC_URLS.get(settlement.network)
        if not rpc_url:
            return []
        token_address = settlement.token_address.lower()
        if not (token_address.startswith("0x") and len(token_address) == 42):
            logger.warning("skipping chain reconciliation for invalid token address: %s", settlement.token_address)
            return []
        worker_wallet = settlement.worker_wallet.lower()
        if not (worker_wallet.startswith("0x") and len(worker_wallet) == 42):
            logger.warning("skipping chain reconciliation for invalid worker wallet: %s", settlement.worker_wallet)
            return []
        decimals = await self._token_decimals(rpc_url, settlement.token_address)
        try:
            amount_units = int((Decimal(str(settlement.amount)) * (Decimal(10) ** decimals)).to_integral_value())
        except (InvalidOperation, ValueError):
            return []
        latest_hex = await self._rpc_call(rpc_url, "eth_blockNumber", [])
        latest_block = int(latest_hex, 16)
        from_block = max(latest_block - 8000, 0)
        logs: list[dict[str, Any]] = []
        step = 1000
        for chunk_start in range(from_block, latest_block + 1, step):
            chunk_end = min(chunk_start + step - 1, latest_block)
            logs.extend(
                await self._rpc_call(
                    rpc_url,
                    "eth_getLogs",
                    [
                        {
                            "fromBlock": hex(chunk_start),
                            "toBlock": hex(chunk_end),
                            "address": token_address,
                            "topics": [TRANSFER_TOPIC, None, self._address_topic(worker_wallet)],
                        }
                    ],
                )
                or []
            )
        candidates: list[dict[str, Any]] = []
        for log in logs or []:
            if int(log.get("data", "0x0"), 16) != amount_units:
                continue
            block_number = int(log["blockNumber"], 16)
            candidates.append(
                {
                    "tx_hash": log["transactionHash"],
                    "block_number": block_number,
                    "log_index": int(log.get("logIndex", "0x0"), 16),
                    "timestamp": await self._block_timestamp(rpc_url, block_number),
                }
            )
        candidates.sort(key=lambda item: (item["timestamp"], item["block_number"], item["log_index"]))
        return candidates

    @staticmethod
    def _match_settlements_to_transfers(
        settlements: list[SettlementRecord],
        candidates: list[dict[str, Any]],
        used_hashes: set[str],
    ) -> list[tuple[SettlementRecord, dict[str, Any]]]:
        matches: list[tuple[SettlementRecord, dict[str, Any]]] = []
        remaining = [candidate for candidate in candidates if candidate["tx_hash"] not in used_hashes]
        for settlement in sorted(settlements, key=lambda item: item.created_at):
            chosen_index = None
            for index, candidate in enumerate(remaining):
                if candidate["timestamp"] >= settlement.created_at - timedelta(minutes=2):
                    chosen_index = index
                    break
            if chosen_index is None and remaining:
                chosen_index = 0
            if chosen_index is None:
                continue
            chosen = remaining.pop(chosen_index)
            matches.append((settlement, chosen))
            used_hashes.add(chosen["tx_hash"])
        return matches

    async def reconcile_settlements_from_chain(self) -> None:
        groups: dict[tuple[str, str, str, str], list[SettlementRecord]] = {}
        used_hashes = {
            item["tx_hash"]
            for item in self.store.settlements()
            if item.get("tx_hash")
        }
        for raw in self.store.settlements():
            settlement = SettlementRecord.model_validate(raw)
            if settlement.tx_hash or settlement.status == SettlementStatus.CONFIRMED:
                continue
            if settlement.status not in {SettlementStatus.TRIGGERED, SettlementStatus.PENDING, SettlementStatus.FAILED}:
                continue
            key = (
                settlement.network,
                settlement.token_address.lower(),
                settlement.worker_wallet.lower(),
                f"{settlement.amount:.18f}",
            )
            groups.setdefault(key, []).append(settlement)

        for settlements in groups.values():
            candidates = await self._candidate_token_transfers(settlements[0])
            for settlement, candidate in self._match_settlements_to_transfers(settlements, candidates, used_hashes):
                self.store_settlement(
                    settlement.model_copy(
                        update={
                            "status": SettlementStatus.CONFIRMED,
                            "tx_hash": candidate["tx_hash"],
                            "failure_reason": None,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )

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
            if settlement.status == SettlementStatus.CONFIRMED:
                continue
            if (
                settlement.network != self.settings.settlement_network
                and settlement.network in {"sepolia", "base-sepolia"}
            ) or (
                settlement.network == self.settings.settlement_network
                and settlement.token_address != self.settings.settlement_token_address
                and settlement.network in {"sepolia", "base-sepolia"}
            ):
                settlement = settlement.model_copy(
                    update={
                        "network": self.settings.settlement_network,
                        "token_address": self.settings.settlement_token_address,
                        "updated_at": datetime.now(UTC),
                    }
                )
                self.store_settlement(settlement)
        await self.reconcile_settlements_from_chain()

    async def fetch_agent_card(self, peer_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{self.settings.axl_node_url}/a2a/{peer_id}")
            response.raise_for_status()
            return response.json()

    def card_supports_coordination(self, card: dict[str, Any]) -> bool:
        skills = {skill.get("id") for skill in card.get("skills", []) if isinstance(skill, dict)}
        return (
            self.settings.nodehub_service_name in skills
            or {"discover_nodes", "request_quote", "propose_lease", "request_job_execution"}.issubset(skills)
        )

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
        return await self.send_coordination_request(
            peer_id,
            "request_job_execution",
            {"envelope": envelope.model_dump(mode="json")},
            fallback_tool_name="request_job_execution",
        )

    async def send_verification_request(self, peer_id: str, envelope: SignedEnvelope) -> dict[str, Any]:
        return await self.send_coordination_request(
            peer_id,
            "request_verification",
            {"envelope": envelope.model_dump(mode="json")},
            fallback_tool_name="request_verification",
        )

    async def submit_attestation(self, peer_id: str, envelope: SignedEnvelope) -> dict[str, Any]:
        return await self.send_coordination_request(
            peer_id,
            "submit_attestation",
            {"envelope": envelope.model_dump(mode="json")},
            fallback_tool_name="submit_attestation",
        )

    async def fetch_remote_receipt(self, peer_id: str, receipt_id: str) -> dict[str, Any]:
        return await self.send_coordination_request(
            peer_id,
            "fetch_receipt",
            {"receipt_id": receipt_id},
            fallback_tool_name="fetch_receipt",
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

    async def handle_lease_proposal(self, envelope: SignedEnvelope) -> SignedEnvelope:
        self.verify_envelope(envelope)
        self.store.append(envelope)
        proposal = LeaseProposal.model_validate(envelope.payload)
        acceptance = LeaseAcceptance(
            lease_id=proposal.lease_id,
            quote_id=proposal.quote_id,
            worker_wallet=self.public_wallet_address(),
            worker_peer_id=self.peer_id,
            accepted=self.settings.daemon_enable_worker,
            reason=None if self.settings.daemon_enable_worker else "worker disabled",
            accepted_at=datetime.now(UTC),
        )
        signed = self.sign_event("lease_acceptance", acceptance.model_dump(mode="json"))
        self.store.append(signed)
        return signed

    async def handle_lease_release(self, envelope: SignedEnvelope) -> SignedEnvelope:
        self.verify_envelope(envelope)
        self.store.append(envelope)
        return self.sign_event("lease_release_ack", {"lease_id": envelope.payload.get("lease_id"), "acknowledged_at": datetime.now(UTC).isoformat()})

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
                        card = await self.fetch_agent_card(peer_id)
                        if not self.card_supports_coordination(card):
                            continue
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
        if tool_name == "request_quote":
            envelope = SignedEnvelope.model_validate(arguments["envelope"])
            result = await self.handle_quote_request(envelope)
            return {"envelope": result.model_dump(mode="json")}
        if tool_name == "propose_lease":
            envelope = SignedEnvelope.model_validate(arguments["envelope"])
            result = await self.handle_lease_proposal(envelope)
            return {"envelope": result.model_dump(mode="json")}
        if tool_name == "release_lease":
            envelope = SignedEnvelope.model_validate(arguments["envelope"])
            result = await self.handle_lease_release(envelope)
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
                {"id": "request_quote", "name": "request_quote"},
                {"id": "propose_lease", "name": "propose_lease"},
                {"id": "release_lease", "name": "release_lease"},
                {"id": "request_job_execution", "name": "request_job_execution"},
                {"id": "request_verification", "name": "request_verification"},
                {"id": "submit_attestation", "name": "submit_attestation"},
                {"id": "fetch_receipt", "name": "fetch_receipt"},
            ],
        }

    async def discover_remote_nodes(self, explicit_peers: list[str] | None = None) -> list[dict[str, Any]]:
        self.store.append(self.current_advertisement_envelope())
        peer_ids = await self.seed_peer_ids(explicit_peers or [])
        for peer_id in peer_ids:
            try:
                card = await self.fetch_agent_card(peer_id)
                if not self.card_supports_coordination(card):
                    continue
                result = await self.send_coordination_request(
                    peer_id,
                    "discover_nodes",
                    {"depth": 0},
                    fallback_tool_name="discover_nodes",
                )
                envelopes = [SignedEnvelope.model_validate(item) for item in result.get("envelopes", [])]
                for envelope in envelopes:
                    self.verify_envelope(envelope)
                self.store.import_many(envelopes)
            except Exception:
                continue
        return self.store.known_nodes()

    async def seed_peer_ids(self, explicit_peers: list[str]) -> list[str]:
        topology = await self.get_topology()
        peer_ids = list(dict.fromkeys(explicit_peers + self.settings.daemon_peer_seeds + self._peer_ids_from_topology(topology) + [item["peer_id"] for item in self.store.known_nodes()]))
        return [peer_id for peer_id in peer_ids if peer_id and peer_id != self.peer_id]

    @staticmethod
    def _peer_ids_from_topology(topology: dict[str, Any]) -> list[str]:
        items: list[str] = []
        for peer in topology.get("peers", []):
            if isinstance(peer, dict):
                value = peer.get("public_key") or peer.get("peer_id") or peer.get("key")
                if value:
                    items.append(value)
            elif isinstance(peer, str):
                items.append(peer)
        for node in topology.get("tree", []):
            if isinstance(node, dict):
                value = node.get("public_key") or node.get("peer_id") or node.get("key")
                if value:
                    items.append(value)
            elif isinstance(node, str):
                items.append(node)
        return items

    async def request_lease(self, payload: LeaseRequestPayload) -> dict[str, Any]:
        await self.discover_remote_nodes([])
        nodes = [
            node
            for node in self.store.known_nodes()
            if node["active"]
            and any(cap["name"] == payload.capability_name.value for cap in node["capabilities"])
            and (not payload.regions or node["region"] in payload.regions)
        ]
        if not nodes:
            raise HTTPException(status_code=404, detail="no matching nodes discovered")

        chosen_by_region: dict[str, dict[str, Any]] = {}
        for node in nodes:
            if payload.regions:
                chosen_by_region.setdefault(node["region"], node)
            else:
                chosen_by_region.setdefault(node["peer_id"], node)

        lease_id = str(uuid4())
        for node in chosen_by_region.values():
            quote_request = QuoteRequest(
                request_id=str(uuid4()),
                requester_wallet=self.assert_identity().wallet_address,
                requester_peer_id=self.peer_id,
                capability_name=payload.capability_name,
                regions=payload.regions,
                inputs={},
                verifier_count=payload.verifier_count,
                lease_duration_seconds=payload.duration_hours * 3600,
            )
            signed_quote_request = self.sign_event("request_quote", quote_request.model_dump(mode="json"))
            self.store.append(signed_quote_request)
            quote_result = await self.send_coordination_request(
                node["peer_id"],
                "request_quote",
                {"envelope": signed_quote_request.model_dump(mode="json")},
                fallback_tool_name="request_quote",
            )
            quote_envelope = SignedEnvelope.model_validate(quote_result["envelope"])
            self.verify_envelope(quote_envelope)
            self.store.append(quote_envelope)
            quote_offer = QuoteOffer.model_validate(quote_envelope.payload)

            proposal = LeaseProposal(
                lease_id=lease_id,
                quote_id=quote_offer.quote_id,
                requester_wallet=self.assert_identity().wallet_address,
                requester_peer_id=self.peer_id,
                worker_wallet=quote_offer.worker_wallet,
                worker_peer_id=quote_offer.worker_peer_id,
                capability_name=payload.capability_name,
                starts_at=datetime.now(UTC),
                ends_at=datetime.now(UTC) + timedelta(hours=payload.duration_hours),
                regions=payload.regions,
                verifier_count=payload.verifier_count,
                payment=quote_offer.payment,
            )
            signed_proposal = self.sign_event("lease_proposal", proposal.model_dump(mode="json"))
            self.store.append(signed_proposal)
            acceptance_result = await self.send_coordination_request(
                node["peer_id"],
                "propose_lease",
                {"envelope": signed_proposal.model_dump(mode="json")},
                fallback_tool_name="propose_lease",
            )
            acceptance_envelope = SignedEnvelope.model_validate(acceptance_result["envelope"])
            self.verify_envelope(acceptance_envelope)
            self.store.append(acceptance_envelope)

        lease = next((item for item in self.store.leases() if item["id"] == lease_id), None)
        if lease is None:
            raise HTTPException(status_code=500, detail="lease was not materialized")
        return lease

    async def release_lease(self, lease_id: str) -> dict[str, Any]:
        lease = next((item for item in self.store.leases() if item["id"] == lease_id), None)
        if lease is None:
            raise HTTPException(status_code=404, detail="lease not found")
        release = LeaseRelease(
            lease_id=lease_id,
            requester_wallet=self.assert_identity().wallet_address,
            requester_peer_id=self.peer_id,
            released_at=datetime.now(UTC),
            reason="released by local client",
        )
        envelope = self.sign_event("lease_release", release.model_dump(mode="json"))
        self.store.append(envelope)
        for peer_id in lease["accepted_peer_ids"]:
            try:
                await self.send_coordination_request(
                    peer_id,
                    "release_lease",
                    {"envelope": envelope.model_dump(mode="json")},
                    fallback_tool_name="release_lease",
                )
            except Exception:
                continue
        refreshed = next((item for item in self.store.leases() if item["id"] == lease_id), None)
        return refreshed or lease

    async def request_job(self, payload: JobRequestPayload) -> dict[str, Any]:
        job_id = str(uuid4())
        discovered_nodes = await self.discover_remote_nodes([])
        try:
            plan = await self.planner.plan(
                job_id=job_id,
                task_type=payload.task_type,
                target_inputs=payload.inputs,
                requested_regions=payload.regions,
                discovered_nodes=discovered_nodes,
                active_leases=self.store.leases(),
                verifier_count=payload.verifier_count,
                explicit_lease_id=payload.lease_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        self.store.append(self.sign_event("job_plan", plan.model_dump(mode="json")))

        selected_workers = list(plan.selected_primary_peer_ids)
        lease_id = plan.selected_lease_id or payload.lease_id
        receipt_ids: list[str] = []
        primary_receipts: list[ExecutionReceipt] = []
        primary_request: ExecutionRequest | None = None
        mirrored_diagnoses: list[DiagnosisSummary] = []
        for worker_peer_id in selected_workers:
            reservation_id = str(uuid4())
            request = ExecutionRequest(
                job_id=job_id,
                reservation_id=reservation_id,
                lease_id=lease_id,
                requester_wallet=self.assert_identity().wallet_address,
                requester_peer_id=self.peer_id,
                worker_peer_id=worker_peer_id,
                task_type=payload.task_type,
                inputs=payload.inputs,
                role=ReservationRole.PRIMARY,
                verification_policy=VerificationPolicy(verifier_count=payload.verifier_count),
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
        for worker_peer_id, receipt_id in zip(plan.selected_verifier_peer_ids, receipt_ids):
            execution_request = ExecutionRequest(
                job_id=job_id,
                reservation_id=str(uuid4()),
                lease_id=lease_id,
                requester_wallet=self.assert_identity().wallet_address,
                requester_peer_id=self.peer_id,
                worker_peer_id=worker_peer_id,
                task_type=payload.task_type,
                inputs=payload.inputs,
                role=ReservationRole.VERIFIER,
                verification_policy=VerificationPolicy(verifier_count=payload.verifier_count),
                payment=self.snapshot_payment_terms(
                    peer_id=worker_peer_id,
                    capability_name=payload.task_type,
                    discovered_nodes=discovered_nodes,
                ),
            )
            verification_request = VerificationRequest(
                verification_id=str(uuid4()),
                execution_request=execution_request,
                primary_receipt_id=receipt_id,
            )
            envelope = self.sign_event("request_verification", verification_request.model_dump(mode="json"))
            self.store.append(envelope)
            verification_result = await self.send_verification_request(worker_peer_id, envelope)
            verification_envelope = SignedEnvelope.model_validate(verification_result["envelope"])
            self.verify_envelope(verification_envelope)
            self.store.append(verification_envelope)
            verification_receipt = VerificationReceipt.model_validate(verification_envelope.payload)
            verification_receipts.append(verification_receipt)
            diagnosis_summary = self.mirror_diagnosis_event(verification_receipt.result)
            if diagnosis_summary is not None:
                mirrored_diagnoses.append(diagnosis_summary)
            await self.request_settlement(self.build_verification_settlement(verification_receipt, verification_request))

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
        primary_by_receipt_id = {receipt.receipt_id: receipt for receipt in primary_receipts}
        for receipt in verification_receipts:
            await self.store_attestation(
                self.make_verification_attestation(
                    receipt,
                    primary_subject_peer_id=primary_by_receipt_id.get(receipt.primary_receipt_id).worker_peer_id
                    if primary_by_receipt_id.get(receipt.primary_receipt_id)
                    else None,
                )
            )

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
                            card = await self.fetch_agent_card(peer_id)
                            if not self.card_supports_coordination(card):
                                continue
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
                self.verify_envelope(envelope)
                self.store.append(envelope)
                return {"jsonrpc": "2.0", "id": payload.id, "result": {"stored": True}}
            if payload.method == "request_quote":
                envelope = SignedEnvelope.model_validate(params["envelope"])
                result = await self.handle_quote_request(envelope)
                return {"jsonrpc": "2.0", "id": payload.id, "result": {"envelope": result.model_dump(mode="json")}}
            if payload.method == "propose_lease":
                envelope = SignedEnvelope.model_validate(params["envelope"])
                result = await self.handle_lease_proposal(envelope)
                return {"jsonrpc": "2.0", "id": payload.id, "result": {"envelope": result.model_dump(mode="json")}}
            if payload.method == "accept_lease":
                return {"jsonrpc": "2.0", "id": payload.id, "result": {"status": "ok"}}
            if payload.method == "reject_lease":
                return {"jsonrpc": "2.0", "id": payload.id, "result": {"status": "ok"}}
            if payload.method == "release_lease":
                envelope = SignedEnvelope.model_validate(params["envelope"])
                result = await self.handle_lease_release(envelope)
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
    app = FastAPI(title="NodeHub Daemon", version="0.2.0")
    runtime = DaemonRuntime(settings or get_settings())
    advertise_task: asyncio.Task | None = None
    discovery_task: asyncio.Task | None = None
    settlement_task: asyncio.Task | None = None

    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime.settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def startup() -> None:
        nonlocal advertise_task, discovery_task, settlement_task
        await runtime.startup()

        async def advertisement_loop() -> None:
            while True:
                runtime.store.append(runtime.current_advertisement_envelope())
                await asyncio.sleep(45)

        async def discovery_loop() -> None:
            await asyncio.sleep(2)
            while True:
                try:
                    await runtime.discover_remote_nodes([])
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
        settlement_task = asyncio.create_task(settlement_loop())

    @app.on_event("shutdown")
    async def shutdown() -> None:
        if advertise_task is not None:
            advertise_task.cancel()
        if discovery_task is not None:
            discovery_task.cancel()
        if settlement_task is not None:
            settlement_task.cancel()
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
        return runtime.store.known_nodes()

    @app.post("/discover")
    async def discover(payload: DiscoverRequest) -> list[dict[str, Any]]:
        return await runtime.discover_remote_nodes(payload.peer_ids)

    @app.get("/leases")
    async def list_leases() -> list[dict[str, Any]]:
        return runtime.store.leases()

    @app.post("/leases/request")
    async def request_lease(payload: LeaseRequestPayload) -> dict[str, Any]:
        return await runtime.request_lease(payload)

    @app.post("/leases/{lease_id}/release")
    async def release_lease(lease_id: str) -> dict[str, Any]:
        return await runtime.release_lease(lease_id)

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
            {"name": "propose_lease", "description": "Submit a signed lease proposal envelope and return a signed lease acceptance."},
            {"name": "release_lease", "description": "Release a signed lease and return the release acknowledgement envelope."},
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
