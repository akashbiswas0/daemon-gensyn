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
        active_leases: list[dict[str, Any]],
        verifier_count: int,
        explicit_lease_id: str | None,
    ) -> JobPlan:
        candidates = self._filter_candidates(discovered_nodes, task_type, requested_regions)
        if not candidates:
            raise ValueError("no matching nodes discovered")

        matching_leases = self._matching_leases(active_leases, task_type, requested_regions)
        fallback = self._fallback_plan(
            job_id=job_id,
            task_type=task_type,
            requested_regions=requested_regions,
            candidates=candidates,
            matching_leases=matching_leases,
            verifier_count=verifier_count,
            explicit_lease_id=explicit_lease_id,
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
        compact_leases = [
            {
                "id": item["id"],
                "accepted_peer_ids": item["accepted_peer_ids"],
                "regions": item["filters"]["regions"],
                "status": item["status"],
            }
            for item in matching_leases
        ]
        suggested = await self.model_client.plan_job(
            {
                "task_type": task_type.value,
                "inputs": target_inputs,
                "requested_regions": requested_regions,
                "verifier_count": verifier_count,
                "explicit_lease_id": explicit_lease_id,
                "candidate_nodes": compact_nodes,
                "active_leases": compact_leases,
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
        use_lease_backed = bool(suggested.get("use_lease_backed"))
        selected_lease_id = suggested.get("selected_lease_id")

        if use_lease_backed and selected_lease_id:
            lease = next((item for item in matching_leases if item["id"] == selected_lease_id), None)
            if lease is not None:
                lease_peers = [peer_id for peer_id in lease["accepted_peer_ids"] if peer_id in allowed_peer_ids]
                if lease_peers:
                    primary_peer_ids = lease_peers[: max(len(requested_regions), 1)]
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
            use_lease_backed=use_lease_backed and bool(selected_lease_id),
            selected_lease_id=selected_lease_id if use_lease_backed else explicit_lease_id,
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

    @staticmethod
    def _matching_leases(
        active_leases: list[dict[str, Any]],
        task_type: CapabilityName,
        requested_regions: list[str],
    ) -> list[dict[str, Any]]:
        return [
            lease
            for lease in active_leases
            if lease["status"] == "active"
            and lease["capability_name"] == task_type.value
            and (not requested_regions or any(region in lease["filters"]["regions"] for region in requested_regions))
            and lease["accepted_peer_ids"]
        ]

    def _fallback_plan(
        self,
        *,
        job_id: str,
        task_type: CapabilityName,
        requested_regions: list[str],
        candidates: list[dict[str, Any]],
        matching_leases: list[dict[str, Any]],
        verifier_count: int,
        explicit_lease_id: str | None,
    ) -> JobPlan:
        selected_lease = None
        if explicit_lease_id:
            selected_lease = next((lease for lease in matching_leases if lease["id"] == explicit_lease_id), None)
        elif matching_leases:
            selected_lease = matching_leases[0]

        primary_peer_ids: list[str] = []
        if selected_lease:
            allowed_peers = {node["peer_id"] for node in candidates}
            primary_peer_ids = [peer_id for peer_id in selected_lease["accepted_peer_ids"] if peer_id in allowed_peers]

        if not primary_peer_ids:
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
        if selected_lease:
            rationale = "Used an active lease-backed peer selection because matching reserved capacity was available."
        elif requested_regions:
            rationale = "Selected one matching peer per requested region using capability and local availability filters."
        else:
            rationale = "Selected the strongest available peer using capability and local availability filters."

        return JobPlan(
            job_id=job_id,
            task_type=task_type,
            requested_regions=requested_regions,
            selected_primary_peer_ids=primary_peer_ids,
            selected_verifier_peer_ids=verifier_peer_ids,
            use_lease_backed=selected_lease is not None,
            selected_lease_id=selected_lease["id"] if selected_lease else explicit_lease_id,
            rationale=rationale,
            verification_requested=verifier_count > 0,
            planner_mode="deterministic",
        )
