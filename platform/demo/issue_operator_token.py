from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct


def ensure_private_key(path: Path) -> bytes:
    if path.exists():
        return path.read_text(encoding="utf-8").strip().encode("utf-8")
    account = Account.create()
    private_key = account.key.hex()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(private_key, encoding="utf-8")
    return private_key.encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue a demo operator token for worker heartbeats.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8010")
    parser.add_argument("--key-file", default="platform/demo/runtime/demo-operator.key")
    parser.add_argument("--json", action="store_true", help="Print the full wallet/token payload as JSON.")
    args = parser.parse_args()

    key_file = Path(args.key_file)
    private_key = ensure_private_key(key_file).decode("utf-8")
    account = Account.from_key(private_key)
    wallet_address = account.address

    with httpx.Client(timeout=15.0) as client:
        challenge = client.post(
            f"{args.api_url}/auth/challenge",
            json={"wallet_address": wallet_address},
        )
        challenge.raise_for_status()
        challenge_message = challenge.json()["challenge"]
        signature = Account.sign_message(
            encode_defunct(text=challenge_message),
            private_key=private_key,
        ).signature.hex()
        verify = client.post(
            f"{args.api_url}/auth/verify",
            json={"wallet_address": wallet_address, "signature": signature},
        )
        verify.raise_for_status()
        token = verify.json()["access_token"]

    payload = {"wallet_address": wallet_address, "access_token": token}
    if args.json:
        print(json.dumps(payload))
    else:
        print(token)


if __name__ == "__main__":
    main()
