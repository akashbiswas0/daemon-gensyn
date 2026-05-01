from functools import lru_cache
from typing import Annotated

from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class PlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NODEHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    daemon_host: str = "127.0.0.1"
    daemon_port: int = 8010
    daemon_state_dir: str = "./platform/daemon-state"
    daemon_enable_worker: bool = True
    daemon_peer_seeds: Annotated[list[str], NoDecode] = Field(default_factory=list)
    wallet_private_key: str = ""
    wallet_private_key_path: str = ""
    worker_payout_wallet: str = ""
    worker_enabled_capabilities: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ]
    )
    axl_node_url: str = "http://127.0.0.1:9002"
    router_url: str = "http://127.0.0.1:9003"
    nodehub_service_name: str = "nodehub"
    worker_service_name: str = "webops-worker"
    worker_public_label: str = "Unnamed Worker"
    worker_region: str = "unknown"
    worker_country_code: str = "XX"
    worker_capacity: int = 2
    worker_price_http_check: float = 0.25
    worker_price_dns_check: float = 0.15
    worker_price_latency_probe: float = 0.15
    worker_price_ping_check: float = 0.10
    worker_price_api_call: float = 0.30
    worker_price_cdn_check: float = 0.20
    keeperhub_enabled: bool = False
    keeperhub_api_key: str = ""
    keeperhub_base_url: str = ""
    keeperhub_workflow_id: str = ""
    keeperhub_trigger_url: str = ""
    keeperhub_network: str = "base-sepolia"
    keeperhub_token_address: str = ""
    keeperhub_poll_interval_seconds: int = 15
    keeperhub_reconcile_interval_seconds: int = 30
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    agentic_enabled: bool = True
    agent_max_followups: int = 2
    agent_max_candidates: int = 8

    @field_validator("daemon_peer_seeds", "cors_allowed_origins", "worker_enabled_capabilities", mode="before")
    @classmethod
    def parse_env_list(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                return value
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @field_validator("worker_payout_wallet")
    @classmethod
    def normalize_payout_wallet(cls, value: str) -> str:
        return value.strip().lower()


@lru_cache(maxsize=1)
def get_settings() -> PlatformSettings:
    return PlatformSettings()
