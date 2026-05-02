from __future__ import annotations

from typing import Any

from daemon.agents.model_client import OpenAIModelClient
from shared.contracts import CapabilityName, JobPlan


class RequesterPlannerAgent:
    def __init__(
        self,
        *,
        model_client: OpenAIModelClient | None,
        max_candidates: int,
        agentic_enabled: bool,
    ) -> None:
        self.model_client = model_client
        self.max_candidates = max_candidates
        self.agentic_enabled = agentic_enabled

    async def plan(
        self,
        *,
        job_id: str,
        task_type: CapabilityName,
        target_inputs: dict[str, Any],
        requested_regions: list[str],
        discovered_nodes: list[dict[str, Any]],
        verifier_count: int,
    ) -> JobPlan:
        candidates = self._filter_candidates(discovered_nodes, task_type, requested_regions)
        if not candidates:
            raise ValueError("no matching nodes discovered")
        fallback = self._fallback_plan(
            job_id=job_id,
            task_type=task_type,
            requested_regions=requested_regions,
            candidates=candidates,
            verifier_count=verifier_count,
        )

        if not self.agentic_enabled or self.model_client is None or not self.model_client.enabled:
            return fallback

        compact_nodes = [
            {
                "peer_id": item["peer_id"],
                "region": item["region"],
                "label": item["label"],
                "reputation_score": item["reputation_score"],
                "capability_names": [cap["name"] for cap in item["capabilities"]],
            }
            for item in candidates
        ]
        suggested = await self.model_client.plan_job(
            {
                "task_type": task_type.value,
                "inputs": target_inputs,
                "requested_regions": requested_regions,
                "verifier_count": verifier_count,
                "candidate_nodes": compact_nodes,
                "fallback_plan": fallback.model_dump(mode="json"),
            }
        )
        if not suggested:
            return fallback

        allowed_peer_ids = {item["peer_id"] for item in candidates}
        primary_peer_ids = [
            peer_id
            for peer_id in suggested.get("primary_peer_ids", [])
            if peer_id in allowed_peer_ids
        ]
        verifier_peer_ids = [
            peer_id
            for peer_id in suggested.get("verifier_peer_ids", [])
            if peer_id in allowed_peer_ids and peer_id not in primary_peer_ids
        ][:verifier_count]
        if not primary_peer_ids:
            return fallback

        rationale = (suggested.get("rationale") or fallback.rationale).strip()
        if verifier_count > 0 and not verifier_peer_ids:
            rationale = f"{fallback.rationale} No additional eligible verifier peer remained after primary selection."

        return JobPlan(
            job_id=job_id,
            task_type=task_type,
            requested_regions=requested_regions,
            selected_primary_peer_ids=primary_peer_ids,
            selected_verifier_peer_ids=verifier_peer_ids,
            use_lease_backed=False,
            selected_lease_id=None,
            rationale=rationale,
            verification_requested=verifier_count > 0,
            planner_mode="openai-assisted",
        )

    def _filter_candidates(
        self,
        discovered_nodes: list[dict[str, Any]],
        task_type: CapabilityName,
        requested_regions: list[str],
    ) -> list[dict[str, Any]]:
        nodes = [
            node
            for node in discovered_nodes
            if node["active"]
            and any(cap["name"] == task_type.value for cap in node["capabilities"])
            and (not requested_regions or node["region"] in requested_regions)
        ]
        nodes.sort(key=lambda item: (item["region"], item["label"]))
        return nodes[: self.max_candidates]

    def _fallback_plan(
        self,
        *,
        job_id: str,
        task_type: CapabilityName,
        requested_regions: list[str],
        candidates: list[dict[str, Any]],
        verifier_count: int,
    ) -> JobPlan:
        primary_peer_ids: list[str] = []
        seen_regions: set[str] = set()
        for node in candidates:
            if requested_regions:
                if node["region"] in seen_regions:
                    continue
                seen_regions.add(node["region"])
            primary_peer_ids.append(node["peer_id"])
            if requested_regions and len(primary_peer_ids) >= len(requested_regions):
                break
        if not requested_regions:
            primary_peer_ids = primary_peer_ids[:1]

        verifier_peer_ids = [
            node["peer_id"]
            for node in candidates
            if node["peer_id"] not in primary_peer_ids
        ][:verifier_count]
        if requested_regions:
            rationale = "Selected one matching peer per requested region using capability and local availability filters."
        else:
            rationale = "Selected the strongest available peer using capability and local availability filters."

        return JobPlan(
            job_id=job_id,
            task_type=task_type,
            requested_regions=requested_regions,
            selected_primary_peer_ids=primary_peer_ids,
            selected_verifier_peer_ids=verifier_peer_ids,
            use_lease_backed=False,
            selected_lease_id=None,
            rationale=rationale,
            verification_requested=verifier_count > 0,
            planner_mode="deterministic",
        )
