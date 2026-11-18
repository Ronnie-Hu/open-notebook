"""Internal lifecycle calls cannot be forged through the public notebook proxy."""

import hashlib
import hmac
import time
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import httpx
import pytest
from fastapi import FastAPI

from api.account_auth import TagentAccountMiddleware
from api.routers import account_lifecycle


def _signed(method: str, path: str, secret: str, timestamp: str) -> dict[str, str]:
    message = f"{method}\n{path}\n{timestamp}".encode()
    return {
        "x-tagent-lifecycle-time": timestamp,
        "x-tagent-lifecycle-signature": hmac.new(
            secret.encode(), message, hashlib.sha256
        ).hexdigest(),
    }


@pytest.mark.asyncio
async def test_lifecycle_route_requires_loopback_and_exact_signed_request(monkeypatch):
    secret = "l" * 48
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_LIFECYCLE_KEY", secret)
    calls = []

    async def fake_disable(account_id):
        calls.append(account_id)

    async def fake_export(account_id, archive):
        assert account_id == account
        archive.writestr("manifest.json", '{"format":"test"}')

    monkeypatch.setattr(account_lifecycle, "disable_account_data", fake_disable)
    monkeypatch.setattr(account_lifecycle, "write_account_archive", fake_export)
    app = FastAPI()
    app.include_router(account_lifecycle.router, prefix="/api")
    protected = TagentAccountMiddleware(
        app,
        verifier_url="http://127.0.0.1:5001",
        cookie_name="__Host-tagent_session",
        public_origin="https://example.org",
    )
    account = uuid4()
    path = f"/api/internal/accounts/{account}/disable"
    timestamp = str(int(time.time()))
    headers = _signed("POST", path, secret, timestamp)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected, client=("127.0.0.1", 1000)),
        base_url="http://localhost",
    ) as client:
        assert (await client.post(path)).status_code == 403
        assert (
            await client.post(
                path, headers={**headers, "x-tagent-lifecycle-signature": "0" * 64}
            )
        ).status_code == 403
        assert (
            await client.post(path, headers=_signed("DELETE", path, secret, timestamp))
        ).status_code == 403
        assert (
            await client.post(
                path, headers=_signed("POST", path, secret, str(int(timestamp) - 31))
            )
        ).status_code == 403
        assert (await client.post(path, headers=headers)).status_code == 200
        export_path = f"/api/internal/accounts/{account}/export"
        exported = await client.get(
            export_path,
            headers=_signed("GET", export_path, secret, timestamp),
        )
        assert exported.status_code == 200
        with ZipFile(BytesIO(exported.content)) as archive:
            assert archive.read("manifest.json") == b'{"format":"test"}'
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected, client=("203.0.113.20", 1000)),
        base_url="http://localhost",
    ) as client:
        assert (await client.post(path, headers=headers)).status_code == 403
    assert calls == [account]
