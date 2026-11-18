"""Carry a verified TAgent account through Surreal Commands jobs."""

import hashlib
import hmac
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar
from uuid import UUID

from surreal_commands import (
    CommandInput,
)
from surreal_commands import (
    execute_command_sync as queue_execute_command_sync,
)
from surreal_commands import (
    get_command_status as queue_get_command_status,
)
from surreal_commands import (
    submit_command as queue_submit_command,
)
from surreal_commands.repository import (
    ensure_record_id as ensure_queue_record_id,
)
from surreal_commands.repository import (
    repo_query as queue_repo_query,
)
from surrealdb import RecordID  # type: ignore

from open_notebook.database.repository import repo_query
from open_notebook.database.tenant_access import (
    account_mode_enabled,
    account_scope,
    current_account,
    current_role,
)
from open_notebook.utils.encryption import get_secret_from_env

_ACCOUNT_KEY = "tagent_account"
_ROLE_KEY = "tagent_role"
_PROOF_KEY = "tagent_proof"
_RESERVED_KEYS = frozenset({_ACCOUNT_KEY, _ROLE_KEY, _PROOF_KEY})
InputT = TypeVar("InputT", bound=CommandInput)
OutputT = TypeVar("OutputT")


def _sign(app: str, name: str, account: UUID, role: str) -> str:
    secret = get_secret_from_env("OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY")
    if not secret or len(secret.encode("utf-8")) < 32:
        raise RuntimeError("Tenant job signing key is missing or too short")
    payload = f"open-notebook-job-v1:{app}:{name}:{account.hex}:{role}"
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _job_context(
    app: str, name: str, context: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if not account_mode_enabled():
        return context
    if context and _RESERVED_KEYS.intersection(context):
        raise PermissionError("Job account context cannot be supplied by caller")
    account = current_account()
    role = current_role()
    if account is None or role not in {"student", "admin"}:
        raise PermissionError("Verified TAgent account is required for jobs")
    return {
        **(context or {}),
        _ACCOUNT_KEY: account.hex,
        _ROLE_KEY: role,
        _PROOF_KEY: _sign(app, name, account, role),
    }


def submit_command(
    app: str,
    command: str,
    args: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Submit a job stamped from server-side account scope, never browser data."""
    return queue_submit_command(app, command, args, _job_context(app, command, context))


def execute_command_sync(
    app: str,
    command: str,
    args: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Any:
    """Preserve account scope when the API waits for a queued command."""
    return queue_execute_command_sync(
        app, command, args, _job_context(app, command, context), **kwargs
    )


def _verified_job_identity(
    app: str, name: str, context: Dict[str, Any]
) -> tuple[UUID, str]:
    try:
        account = UUID(str(context[_ACCOUNT_KEY]))
        role = str(context[_ROLE_KEY])
        proof = str(context[_PROOF_KEY])
    except (KeyError, ValueError, TypeError) as error:
        raise PermissionError("Account job context is invalid") from error
    if role not in {"student", "admin"}:
        raise PermissionError("Account job role is invalid")
    if not hmac.compare_digest(proof, _sign(app, name, account, role)):
        raise PermissionError("Account job proof is invalid")
    return account, role


async def get_command_status(command_id: str) -> Any:
    """Hide queue results unless the current account owns the signed job."""
    if not account_mode_enabled():
        return await queue_get_command_status(command_id)
    actor = current_account()
    if actor is None:
        raise PermissionError("Verified TAgent account is required for job status")
    rows = await queue_repo_query(
        "SELECT app, name, context FROM $command_id",
        {"command_id": ensure_queue_record_id(command_id)},
    )
    if not rows:
        raise ValueError("Command not found")
    job = rows[0]
    try:
        account, _ = _verified_job_identity(
            str(job["app"]), str(job["name"]), job["context"]
        )
    except (KeyError, TypeError, PermissionError) as error:
        raise ValueError("Command not found") from error
    if account != actor:
        raise ValueError("Command not found")
    return await queue_get_command_status(command_id)


def tenant_command_handler(
    func: Callable[[InputT], Awaitable[OutputT]],
) -> Callable[[InputT], Awaitable[OutputT]]:
    """Reject unsigned jobs in account mode and bind their verified owner."""

    @wraps(func)
    async def wrapped(input_data: InputT) -> OutputT:
        execution = input_data.execution_context
        context = execution.user_context if execution else None
        if not account_mode_enabled():
            if isinstance(context, dict) and _RESERVED_KEYS.intersection(context):
                raise RuntimeError("Account job reached an anonymous worker")
            return await func(input_data)
        if not execution or not isinstance(context, dict):
            raise PermissionError("Account job context is missing")
        account, role = _verified_job_identity(
            execution.app_name, execution.command_name, context
        )
        with account_scope(account, role=role):
            # TAgent can mark an account for deletion after a job was queued.
            # Check the current OpenNotebook account record before any job code
            # runs; never cache this status across jobs.
            rows = await repo_query(
                "SELECT active FROM $account",
                {"account": RecordID("account", account.hex)},
            )
            if not rows or rows[0].get("active") is not True:
                raise PermissionError("Account is disabled for background jobs")
            return await func(input_data)

    return wrapped
