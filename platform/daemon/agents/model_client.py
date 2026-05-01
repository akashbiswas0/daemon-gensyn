from __future__ import annotations

import json
from typing import Any

import httpx

from daemon.agents.prompts import (
    DIAGNOSER_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    REPORTER_SYSTEM_PROMPT,
)


def _extract_output_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text
    raise ValueError("missing structured model output")


class OpenAIModelClient:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _structured_call(
        self,
        *,
        system_prompt: str,
        payload: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
        timeout_seconds: float = 12.0,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        body = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": json.dumps(payload, separators=(",", ":"))}],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                response.raise_for_status()
            raw = response.json()
            return json.loads(_extract_output_text(raw))
        except Exception:
            return None

    async def plan_job(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "primary_peer_ids": {"type": "array", "items": {"type": "string"}},
                "verifier_peer_ids": {"type": "array", "items": {"type": "string"}},
                "use_lease_backed": {"type": "boolean"},
                "selected_lease_id": {"type": ["string", "null"]},
                "rationale": {"type": "string"},
            },
            "required": [
                "primary_peer_ids",
                "verifier_peer_ids",
                "use_lease_backed",
                "selected_lease_id",
                "rationale",
            ],
        }
        return await self._structured_call(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            payload=payload,
            schema_name="nodehub_job_plan",
            schema=schema,
        )

    async def diagnose_failure(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "diagnosis": {"type": "string"},
                "confidence": {"type": "number"},
                "suggested_next_step": {"type": ["string", "null"]},
                "follow_up_summary": {"type": ["string", "null"]},
            },
            "required": ["diagnosis", "confidence", "suggested_next_step", "follow_up_summary"],
        }
        return await self._structured_call(
            system_prompt=DIAGNOSER_SYSTEM_PROMPT,
            payload=payload,
            schema_name="nodehub_diagnosis",
            schema=schema,
        )

    async def summarize_report(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "final_summary": {"type": "string", "maxLength": 220},
                "confidence": {"type": "number"},
                "issue_scope": {"type": "string", "enum": ["regional", "global", "inconclusive"]},
                "verifier_summary": {"type": ["string", "null"], "maxLength": 120},
                "report_labels": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "final_summary",
                "confidence",
                "issue_scope",
                "verifier_summary",
                "report_labels",
            ],
        }
        return await self._structured_call(
            system_prompt=REPORTER_SYSTEM_PROMPT,
            payload=payload,
            schema_name="nodehub_report_summary",
            schema=schema,
        )
