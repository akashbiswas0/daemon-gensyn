from __future__ import annotations

from typing import Any

import httpx

from shared.contracts import SettlementRecord


class KeeperHubClient:
    def __init__(
        self,
        *,
        enabled: bool,
        api_key: str,
        base_url: str,
        workflow_id: str,
        trigger_url: str,
        network: str,
        token_address: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.trigger_url = trigger_url
        self.enabled = enabled and bool(token_address) and bool(
            trigger_url or (api_key and base_url and workflow_id)
        )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.workflow_id = workflow_id
        self.network = network
        self.token_address = token_address
        self.transport = transport

    async def trigger_payout(self, settlement: SettlementRecord) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post(self.trigger_endpoint, json=self.trigger_body(settlement))
            response.raise_for_status()
            return response.json()

    async def poll_run(self, run_id: str) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.get(f"{self.base_url}/runs/{run_id}")
            response.raise_for_status()
            return response.json()

    @staticmethod
    def extract_run_id(payload: dict[str, Any]) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("run_id", "runId", "executionId", "execution_id", "id"):
            if isinstance(payload.get(key), str) and payload.get(key):
                return payload[key]
        for parent_key in ("run", "data", "result"):
            nested = payload.get(parent_key)
            if isinstance(nested, dict):
                run_id = KeeperHubClient.extract_run_id(nested)
                if run_id:
                    return run_id
        return None

    @staticmethod
    def extract_status(payload: dict[str, Any]) -> str | None:
        if not isinstance(payload, dict):
            return None
        status = payload.get("status")
        if isinstance(status, str) and status:
            return status.lower()
        for parent_key in ("run", "data", "result"):
            nested = payload.get(parent_key)
            if isinstance(nested, dict):
                nested_status = KeeperHubClient.extract_status(nested)
                if nested_status:
                    return nested_status
        return None

    @staticmethod
    def extract_tx_hash(payload: dict[str, Any]) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("tx_hash", "txHash", "transaction_hash", "transactionHash", "hash"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        for parent_key in ("run", "data", "result", "transaction"):
            nested = payload.get(parent_key)
            if isinstance(nested, dict):
                tx_hash = KeeperHubClient.extract_tx_hash(nested)
                if tx_hash:
                    return tx_hash
        return None

    @staticmethod
    def is_terminal_success(status: str | None) -> bool:
        return status in {"confirmed", "completed", "success", "succeeded"}

    @staticmethod
    def is_terminal_failure(status: str | None) -> bool:
        return status in {"failed", "error", "cancelled", "canceled"}

    @property
    def trigger_endpoint(self) -> str:
        if self.trigger_url:
            return self.trigger_url
        return f"{self.base_url}/workflows/{self.workflow_id}/trigger"

    @property
    def uses_webhook_trigger(self) -> bool:
        return bool(self.trigger_url)

    def trigger_body(self, settlement: SettlementRecord) -> dict[str, Any]:
        payload = self._trigger_payload(settlement)
        if self.uses_webhook_trigger:
            return payload
        return {
            "input": payload,
            "metadata": {
                "settlement_id": settlement.settlement_id,
                "job_id": settlement.job_id,
                "receipt_id": settlement.receipt_id,
            },
        }

    def _trigger_payload(self, settlement: SettlementRecord) -> dict[str, Any]:
        return {
            "network": self.network,
            "token_address": self.token_address,
            "currency": settlement.currency,
            "amount": settlement.amount,
            "to": settlement.worker_wallet,
            "settlement_id": settlement.settlement_id,
            "job_id": settlement.job_id,
            "receipt_id": settlement.receipt_id,
            "worker_peer_id": settlement.worker_peer_id,
            "role": settlement.role.value,
            "capability_name": settlement.capability_name.value,
        }

    def _client(self) -> httpx.AsyncClient:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return httpx.AsyncClient(
            timeout=30.0,
            transport=self.transport,
            headers=headers,
        )
