"""Short-lived SurrealDB record identity for a verified TAgent account."""

import base64
import hashlib
import hmac
import json
import time
from uuid import UUID


def account_record_id(account_id: UUID) -> str:
    return f"account:{account_id.hex}"


def sign_record_token(
    account_id: UUID, *, namespace: str, database: str, secret: str
) -> str:
    """Sign a 60-second token for SurrealDB's `tagent_account` access method.

    Keep the secret server-side. Never return this token to the browser.
    """
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("Tenant signing secret must contain at least 32 bytes")
    if not namespace or not database:
        raise ValueError("SurrealDB namespace and database are required")

    def encode(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    issued_at = int(time.time())
    header = encode({"alg": "HS512", "typ": "JWT"})
    claims = encode(
        {
            "NS": namespace,
            "DB": database,
            "AC": "tagent_account",
            "ID": account_record_id(account_id),
            "iat": issued_at,
            "exp": issued_at + 60,
        }
    )
    signed_part = f"{header}.{claims}"
    digest = hmac.new(
        secret.encode("utf-8"), signed_part.encode("ascii"), hashlib.sha512
    ).digest()
    signature = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"{signed_part}.{signature}"
