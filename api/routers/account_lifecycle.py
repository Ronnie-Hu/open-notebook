"""Loopback-only, signed account cleanup calls from the TAgent identity service."""

import hashlib
import hmac
import re
import tempfile
import time
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from open_notebook.account_export import write_account_archive
from open_notebook.account_purge import (
    AccountPurgePending,
    disable_account_data,
    purge_disabled_account,
)
from open_notebook.database.tenant_access import account_mode_enabled
from open_notebook.utils.encryption import get_secret_from_env

router = APIRouter(prefix="/internal/accounts", tags=["internal-account-lifecycle"])
_SIGNATURE = re.compile(r"^[0-9a-f]{64}$")


def _authorize(request: Request) -> None:
    if (
        not account_mode_enabled()
        or not request.client
        or request.client.host
        not in {
            "127.0.0.1",
            "::1",
        }
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    secret = get_secret_from_env("OPEN_NOTEBOOK_TAGENT_LIFECYCLE_KEY")
    if not secret or len(secret.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="Account lifecycle unavailable")
    timestamp = request.headers.get("x-tagent-lifecycle-time", "")
    signature = request.headers.get("x-tagent-lifecycle-signature", "")
    if (
        len(timestamp) != 10
        or not timestamp.isdecimal()
        or not _SIGNATURE.fullmatch(signature)
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    if abs(int(time.time()) - int(timestamp)) > 30:
        raise HTTPException(status_code=403, detail="Forbidden")
    message = f"{request.method}\n{request.url.path}\n{timestamp}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/{account_id}/disable", include_in_schema=False)
async def disable_account(account_id: UUID, request: Request):
    _authorize(request)
    try:
        await disable_account_data(account_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail="Forbidden") from error
    return {"disabled": True}


@router.delete("/{account_id}/purge", include_in_schema=False)
async def purge_account(account_id: UUID, request: Request):
    _authorize(request)
    try:
        remaining = await purge_disabled_account(account_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail="Forbidden") from error
    except AccountPurgePending:
        return JSONResponse(status_code=202, content={"pending": True})
    return {"deleted": True, "remaining": remaining.as_dict()}


@router.get("/{account_id}/export", include_in_schema=False)
async def export_account(account_id: UUID, request: Request):
    _authorize(request)
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            await write_account_archive(account_id, archive)
        output.seek(0)
    except PermissionError as error:
        output.close()
        raise HTTPException(status_code=403, detail="Forbidden") from error
    except Exception:
        output.close()
        raise

    def chunks():
        try:
            while chunk := output.read(64 * 1024):
                yield chunk
        finally:
            output.close()

    return StreamingResponse(
        chunks(),
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="open-notebook-account.zip"',
        },
    )
