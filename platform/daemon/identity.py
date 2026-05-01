from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from eth_account import Account
from eth_account.messages import encode_defunct

from shared.contracts import SignedEnvelope


def _canonical_json(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def _message_body(
    *,
    event_id: str,
    event_type: str,
    signer_wallet: str,
    signer_peer_id: str,
    timestamp: datetime,
    payload: dict,
) -> str:
    return _canonical_json(
        {
            "event_id": event_id,
            "event_type": event_type,
            "signer_wallet": signer_wallet.lower(),
            "signer_peer_id": signer_peer_id,
            "timestamp": timestamp.astimezone(UTC).isoformat(),
            "payload": payload,
        }
    )


@dataclass(slots=True)
class LocalIdentity:
    wallet_address: str
    private_key_hex: str
    peer_id: str

    @classmethod
    def load(
        cls,
        *,
        state_dir: str,
        peer_id: str,
        private_key: str = "",
        private_key_path: str = "",
    ) -> "LocalIdentity":
        state_path = Path(state_dir)
        state_path.mkdir(parents=True, exist_ok=True)
        key_path = state_path / "identity.key"
        raw_key = private_key.strip()
        if not raw_key and private_key_path:
            raw_key = Path(private_key_path).read_text(encoding="utf-8").strip()
        if not raw_key:
            if key_path.exists():
                raw_key = key_path.read_text(encoding="utf-8").strip()
            else:
                raw_key = Account.create().key.hex()
                key_path.write_text(raw_key, encoding="utf-8")
        account = Account.from_key(raw_key)
        if not key_path.exists():
            key_path.write_text(raw_key, encoding="utf-8")
        return cls(
            wallet_address=account.address.lower(),
            private_key_hex=raw_key,
            peer_id=peer_id,
        )

    def sign_envelope(self, event_type: str, payload: dict) -> SignedEnvelope:
        timestamp = datetime.now(UTC)
        event_id = str(uuid4())
        message = _message_body(
            event_id=event_id,
            event_type=event_type,
            signer_wallet=self.wallet_address,
            signer_peer_id=self.peer_id,
            timestamp=timestamp,
            payload=payload,
        )
        signature = Account.sign_message(
            encode_defunct(text=message),
            private_key=self.private_key_hex,
        ).signature.hex()
        return SignedEnvelope(
            event_id=event_id,
            event_type=event_type,
            signer_wallet=self.wallet_address,
            signer_peer_id=self.peer_id,
            timestamp=timestamp,
            payload=payload,
            signature=signature,
        )

    @staticmethod
    def verify_envelope(envelope: SignedEnvelope) -> bool:
        message = _message_body(
            event_id=envelope.event_id,
            event_type=envelope.event_type,
            signer_wallet=envelope.signer_wallet,
            signer_peer_id=envelope.signer_peer_id,
            timestamp=envelope.timestamp,
            payload=envelope.payload,
        )
        recovered = Account.recover_message(
            encode_defunct(text=message),
            signature=envelope.signature,
        ).lower()
        return recovered == envelope.signer_wallet.lower()
