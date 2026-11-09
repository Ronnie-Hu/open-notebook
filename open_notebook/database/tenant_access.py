"""Short-lived SurrealDB record identity for a verified TAgent account."""

import base64
import hashlib
import hmac
import json
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator
from uuid import UUID

_account: ContextVar[UUID | None] = ContextVar(
    "open_notebook_tagent_account", default=None
)
_system_operation: ContextVar[bool] = ContextVar(
    "open_notebook_tagent_system_operation", default=False
)


def account_mode_enabled() -> bool:
    return os.getenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE") == "1"


@contextmanager
def account_scope(account_id: UUID) -> Iterator[None]:
    """Bind a verified TAgent account for downstream DB calls in this task."""
    token = _account.set(account_id)
    try:
        yield
    finally:
        _account.reset(token)


@contextmanager
def migration_scope() -> Iterator[None]:
    """Allow elevated DB access only during the explicit startup migration."""
    token = _system_operation.set(True)
    try:
        yield
    finally:
        _system_operation.reset(token)


def current_account() -> UUID | None:
    return _account.get()


def in_migration_scope() -> bool:
    return _system_operation.get()


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
