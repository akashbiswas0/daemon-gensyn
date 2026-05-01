from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class CapabilityName(str, Enum):
    DESCRIBE_NODE = "describe_node"
    HTTP_CHECK = "http_check"
    DNS_CHECK = "dns_check"
    LATENCY_PROBE = "latency_probe"
    PING_CHECK = "ping_check"
    API_CALL = "api_call"
    CDN_CHECK = "cdn_check"
    DIAGNOSE_FAILURE = "diagnose_failure"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class LeaseStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    RELEASED = "released"
    CANCELED = "canceled"
    EXPIRED = "expired"


class ReservationRole(str, Enum):
    PRIMARY = "primary"
    VERIFIER = "verifier"


class ReservationStatus(str, Enum):
    RESERVED = "reserved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RELEASED = "released"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    INCONCLUSIVE = "inconclusive"
    MISMATCH = "mismatch"


class SettlementStatus(str, Enum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class StructuredFailure(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class NodeCapability(BaseModel):
    name: CapabilityName
    description: str
    price_per_invocation: float
    unit: str = "request"


class NodeListing(BaseModel):
    id: str | None = None
    owner_wallet: str
    peer_id: str
    label: str
    region: str
    country_code: str
    capabilities: list[NodeCapability]
    max_concurrency: int
    active: bool = True
    reputation_score: float = 1.0
    pricing_notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    last_seen_at: datetime | None = None


class LeaseFilter(BaseModel):
    regions: list[str] = Field(default_factory=list)
    country_codes: list[str] = Field(default_factory=list)
    capability_names: list[CapabilityName] = Field(default_factory=list)
    max_price_per_node: float | None = None


class LeaseWindow(BaseModel):
    starts_at: datetime
    ends_at: datetime


class Reservation(BaseModel):
    id: str | None = None
    lease_id: str | None = None
    job_id: str | None = None
    node_id: str
    peer_id: str
    role: ReservationRole
    status: ReservationStatus = ReservationStatus.RESERVED
    reserved_from: datetime
    reserved_until: datetime
    quoted_price: float


class Lease(BaseModel):
    id: str | None = None
    customer_wallet: str
    status: LeaseStatus = LeaseStatus.PENDING
    filters: LeaseFilter
    reservation_ids: list[str] = Field(default_factory=list)
    lease_window: LeaseWindow
    payment_reference: str


class VerificationPolicy(BaseModel):
    verifier_count: int = 1
    strategy: Literal["rerun", "subset"] = "rerun"
    tolerance_ms: float = 75.0


class HttpCheckInput(BaseModel):
    url: HttpUrl
    method: Literal["GET", "HEAD"] = "GET"
    timeout_seconds: float = 10.0
    headers: dict[str, str] = Field(default_factory=dict)


class DNSCheckInput(BaseModel):
    hostname: str
    port: int = 443


class LatencyProbeInput(BaseModel):
    host: str
    port: int = 443
    timeout_seconds: float = 5.0


class PingCheckInput(BaseModel):
    host: str
    count: int = Field(default=3, ge=1, le=5)
    timeout_seconds: float = 8.0


class APICallInput(BaseModel):
    url: HttpUrl
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = "GET"
    timeout_seconds: float = 15.0
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: dict[str, Any] | list[Any] | None = None
    raw_body: str | None = None
    expected_statuses: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_body(self) -> "APICallInput":
        if self.json_body is not None and self.raw_body is not None:
            raise ValueError("Provide either json_body or raw_body, not both.")
        return self


class CDNCheckInput(BaseModel):
    url: HttpUrl
    method: Literal["HEAD", "GET"] = "HEAD"
    timeout_seconds: float = 10.0
    headers: dict[str, str] = Field(default_factory=dict)


TaskInput = HttpCheckInput | DNSCheckInput | LatencyProbeInput | PingCheckInput | APICallInput | CDNCheckInput


class TaskRequest(BaseModel):
    job_id: str
    reservation_id: str
    task_type: CapabilityName
    inputs: dict[str, Any]
    verification_policy: VerificationPolicy


class TaskMeasurement(BaseModel):
    status_code: int | None = None
    response_time_ms: float | None = None
    latency_ms: float | None = None
    packet_loss_percent: float | None = None
    dns_answers: list[str] = Field(default_factory=list)
    resolved_url: str | None = None
    provider: str | None = None
    cache_status: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class TaskResult(BaseModel):
    job_id: str
    reservation_id: str
    task_type: CapabilityName
    node_peer_id: str
    node_region: str
    success: bool
    measurement: TaskMeasurement = Field(default_factory=TaskMeasurement)
    failure: StructuredFailure | None = None
    started_at: datetime
    completed_at: datetime
    raw: dict[str, Any] = Field(default_factory=dict)
    diagnosis: str | None = None
    confidence: float | None = None


class VerificationResult(BaseModel):
    status: VerificationStatus
    primary_reservation_id: str
    compared_reservation_ids: list[str] = Field(default_factory=list)
    notes: str | None = None
    delta_ms: float | None = None


class Job(BaseModel):
    id: str | None = None
    customer_wallet: str
    task_type: CapabilityName
    inputs: dict[str, Any]
    status: JobStatus = JobStatus.QUEUED
    regions: list[str] = Field(default_factory=list)
    selected_node_ids: list[str] = Field(default_factory=list)
    lease_id: str | None = None
    payment_reference: str
    verification_policy: VerificationPolicy = Field(default_factory=VerificationPolicy)


class JobReport(BaseModel):
    job_id: str
    status: JobStatus
    results: list[TaskResult] = Field(default_factory=list)
    verification: list[VerificationResult] = Field(default_factory=list)
    summary: str | None = None
    created_at: datetime
    updated_at: datetime


class OperatorBalance(BaseModel):
    wallet_address: str
    pending_credits: float = 0.0
    settled_credits: float = 0.0
    total_earned: float = 0.0


class WalletChallenge(BaseModel):
    wallet_address: str
    challenge: str
    expires_at: datetime


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class SignedEnvelope(BaseModel):
    event_id: str
    event_type: str
    signer_wallet: str
    signer_peer_id: str
    timestamp: datetime
    payload: dict[str, Any]
    signature: str


class PaymentTerms(BaseModel):
    quoted_price: float | None = None
    currency: str | None = None
    payment_terms: str | None = None


class NodeAdvertisement(BaseModel):
    peer_id: str
    wallet_address: str
    label: str
    region: str
    country_code: str
    capabilities: list[NodeCapability]
    max_concurrency: int
    active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = 120
    payment: PaymentTerms = Field(default_factory=PaymentTerms)


class QuoteRequest(BaseModel):
    request_id: str
    requester_wallet: str
    requester_peer_id: str
    capability_name: CapabilityName
    regions: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    verifier_count: int = 0
    lease_duration_seconds: int = 3600
    max_price_per_node: float | None = None


class QuoteOffer(BaseModel):
    quote_id: str
    request_id: str
    worker_wallet: str
    worker_peer_id: str
    capability_name: CapabilityName
    region: str
    country_code: str
    available_capacity: int
    expires_at: datetime
    payment: PaymentTerms = Field(default_factory=PaymentTerms)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LeaseProposal(BaseModel):
    lease_id: str
    quote_id: str
    requester_wallet: str
    requester_peer_id: str
    worker_wallet: str
    worker_peer_id: str
    capability_name: CapabilityName
    starts_at: datetime
    ends_at: datetime
    regions: list[str] = Field(default_factory=list)
    verifier_count: int = 0
    payment: PaymentTerms = Field(default_factory=PaymentTerms)


class LeaseAcceptance(BaseModel):
    lease_id: str
    quote_id: str
    worker_wallet: str
    worker_peer_id: str
    accepted: bool
    reason: str | None = None
    accepted_at: datetime


class LeaseRelease(BaseModel):
    lease_id: str
    requester_wallet: str
    requester_peer_id: str
    released_at: datetime
    reason: str | None = None


class ExecutionRequest(BaseModel):
    job_id: str
    reservation_id: str | None = None
    lease_id: str | None = None
    quote_id: str | None = None
    requester_wallet: str
    requester_peer_id: str
    worker_peer_id: str
    task_type: CapabilityName
    inputs: dict[str, Any]
    role: ReservationRole = ReservationRole.PRIMARY
    verification_policy: VerificationPolicy = Field(default_factory=VerificationPolicy)
    payment: PaymentTerms = Field(default_factory=PaymentTerms)

    @model_validator(mode="after")
    def ensure_reservation_id(self) -> "ExecutionRequest":
        if not self.reservation_id:
            self.reservation_id = self.lease_id or self.quote_id or self.job_id
        return self


class ExecutionReceipt(BaseModel):
    receipt_id: str
    job_id: str
    lease_id: str | None = None
    quote_id: str | None = None
    requester_wallet: str
    requester_peer_id: str
    worker_wallet: str
    worker_peer_id: str
    role: ReservationRole
    result: TaskResult
    payment: PaymentTerms = Field(default_factory=PaymentTerms)


class VerificationRequest(BaseModel):
    verification_id: str
    execution_request: ExecutionRequest
    primary_receipt_id: str


class VerificationReceipt(BaseModel):
    receipt_id: str
    verification_id: str
    primary_receipt_id: str
    verifier_wallet: str
    verifier_peer_id: str
    result: TaskResult
    status: VerificationStatus
    notes: str | None = None


class JobPlan(BaseModel):
    job_id: str
    task_type: CapabilityName
    requested_regions: list[str] = Field(default_factory=list)
    selected_primary_peer_ids: list[str] = Field(default_factory=list)
    selected_verifier_peer_ids: list[str] = Field(default_factory=list)
    use_lease_backed: bool = False
    selected_lease_id: str | None = None
    rationale: str
    verification_requested: bool = False
    planner_mode: Literal["deterministic", "openai-assisted"] = "deterministic"


class DiagnosisSummary(BaseModel):
    job_id: str
    reservation_id: str
    task_type: CapabilityName
    node_peer_id: str
    node_region: str
    diagnosis: str
    confidence: float
    suggested_next_step: str | None = None
    follow_up_summary: str | None = None
    follow_up_results: dict[str, Any] = Field(default_factory=dict)
    source: Literal["deterministic", "openai-assisted"] = "deterministic"


class ReportSummary(BaseModel):
    job_id: str
    final_summary: str
    confidence: float
    issue_scope: Literal["regional", "global", "inconclusive"] = "inconclusive"
    verifier_summary: str | None = None
    report_labels: list[str] = Field(default_factory=list)
    source: Literal["deterministic", "openai-assisted"] = "deterministic"
    summary_mode: Literal["compact"] = "compact"


class Attestation(BaseModel):
    attestation_id: str
    subject_peer_id: str
    issuer_wallet: str
    issuer_peer_id: str
    job_id: str | None = None
    receipt_id: str | None = None
    verdict: Literal["satisfied", "verified", "mismatch", "rejected"]
    notes: str | None = None
    created_at: datetime


class SettlementRecord(BaseModel):
    settlement_id: str
    job_id: str
    receipt_id: str
    worker_peer_id: str
    worker_wallet: str
    role: ReservationRole
    capability_name: CapabilityName
    amount: float
    currency: str
    token_address: str
    network: str = "base-sepolia"
    status: SettlementStatus = SettlementStatus.PENDING
    keeperhub_run_id: str | None = None
    tx_hash: str | None = None
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class AnchorProbe(BaseModel):
    anchor_id: str
    provider: str = "public"
    host: str
    port: int
    latitude: float
    longitude: float
    rtt_ms: float | None = None
    error: str | None = None
    measured_at: datetime

    @model_validator(mode="before")
    @classmethod
    def infer_provider_for_legacy_records(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if value.get("provider"):
            return value

        anchor_id = str(value.get("anchor_id", ""))
        host = str(value.get("host", ""))
        if anchor_id.startswith("aws-") or "amazonaws.com" in host:
            value["provider"] = "public-a"
        elif anchor_id.startswith("gcp-") or "googleapis.com" in host:
            value["provider"] = "public-b"
        else:
            value["provider"] = "public"
        return value


class GeoObservation(BaseModel):
    observation_id: str
    subject_peer_id: str
    observer_peer_id: str
    observer_wallet: str
    claimed_region: str
    claimed_country_code: str
    self_attestation: bool
    probes: list[AnchorProbe] = Field(default_factory=list)
    measured_at: datetime


class RegionTrustVerdict(str, Enum):
    VERIFIED = "verified"
    PROBABLE = "probable"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"


class RegionTrust(BaseModel):
    subject_peer_id: str
    claimed_region: str
    verdict: RegionTrustVerdict
    confidence: float = 0.0
    successful_probe_count: int = 0
    successful_provider_count: int = 0
    impossible_anchor_ids: list[str] = Field(default_factory=list)
    best_fit_region: str | None = None
    best_fit_distance_km: float | None = None
    claimed_residual_ms: float | None = None
    best_residual_ms: float | None = None
    last_observed_at: datetime | None = None
    notes: str = ""


class LocalEventRecord(BaseModel):
    envelope: SignedEnvelope
    stored_at: datetime
