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
_role: ContextVar[str | None] = ContextVar("open_notebook_tagent_role", default=None)
_system_operation: ContextVar[bool] = ContextVar(
    "open_notebook_tagent_system_operation", default=False
)


def account_mode_enabled() -> bool:
    return os.getenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE") == "1"


@contextmanager
def account_scope(account_id: UUID, *, role: str = "student") -> Iterator[None]:
    """Bind a verified TAgent account for downstream DB calls in this task."""
    if role not in {"student", "admin"}:
        raise ValueError("Unrecognized TAgent account role")
    token = _account.set(account_id)
    role_token = _role.set(role)
    try:
        yield
    finally:
        _role.reset(role_token)
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


def current_role() -> str | None:
    return _role.get()


def student_account_mode() -> bool:
    return account_mode_enabled() and current_role() != "admin"


def in_migration_scope() -> bool:
    return _system_operation.get()


def account_record_id(account_id: UUID) -> str:
    return f"account:{account_id.hex}"


def scoped_singleton_record_id(record_id: str) -> str:
    """Keep the legacy administrator's singleton; give each other account its own."""
    if not account_mode_enabled():
        return record_id
    actor = current_account()
    if actor is None:
        raise PermissionError("Verified TAgent account is required for settings")
    legacy_admin = os.getenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID")
    if not legacy_admin:
        raise RuntimeError("Legacy administrator ID is required in account mode")
    try:
        if actor == UUID(legacy_admin):
            return record_id
    except ValueError as error:
        raise RuntimeError("Legacy administrator ID is invalid") from error
    table, separator, key = record_id.partition(":")
    if not separator or not table or not key:
        raise ValueError("Singleton record ID must include a table and key")
    return f"{table}:{key}_{actor.hex}"


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
