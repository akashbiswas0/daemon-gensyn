from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from shared.contracts import CapabilityName, StructuredFailure, TaskResult


class TaskPlugin(ABC):
    name: CapabilityName
    description: str

    @abstractmethod
    async def execute(self, inputs: dict[str, Any], *, job_id: str, reservation_id: str, node_peer_id: str, node_region: str) -> TaskResult:
        raise NotImplementedError

    async def verify(self, primary: TaskResult, candidate: TaskResult) -> tuple[bool, str]:
        if primary.success != candidate.success:
            return False, "success state mismatch"
        return True, "results aligned"

    def failure(self, code: str, message: str, *, retryable: bool = False, details: dict[str, Any] | None = None) -> StructuredFailure:
        return StructuredFailure(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
