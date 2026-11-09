"""The TAgent session verifier rejects forged and unavailable identities."""

import json
from uuid import UUID

import httpx
import pytest

from api.tagent_identity import SessionUnavailable, verify_tagent_session
from open_notebook.database.tenant_access import account_record_id, sign_record_token

ACCOUNT_ID = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")


def _transport(status: int, body: dict | None = None) -> httpx.MockTransport:
    def reply(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:5001/auth/me"
        assert request.headers["cookie"] == "__Host-tagent_session=opaque-session"
        assert "x-tagent-account-id" not in request.headers
        return httpx.Response(status, json=body)

    return httpx.MockTransport(reply)


def _body(**overrides: object) -> dict:
    user = {
        "id": str(ACCOUNT_ID),
        "role": "student",
        "active": True,
        "deletion_pending": False,
    }
    user.update(overrides)
    return {"user": user, "csrf": "opaque-csrf"}


@pytest.mark.asyncio
async def test_verified_session_maps_to_account() -> None:
    actor = await verify_tagent_session(
        session_cookie="opaque-session",
        cookie_name="__Host-tagent_session",
        base_url="http://127.0.0.1:5001",
        transport=_transport(200, _body()),
    )
    assert actor is not None
    assert actor.account_id == ACCOUNT_ID
    assert actor.role == "student"


@pytest.mark.asyncio
async def test_unauthorized_and_missing_session() -> None:
    kwargs = {
        "cookie_name": "__Host-tagent_session",
        "base_url": "http://127.0.0.1:5001",
        "transport": _transport(401),
    }
    assert (
        await verify_tagent_session(session_cookie="opaque-session", **kwargs) is None
    )
    assert await verify_tagent_session(session_cookie="", **kwargs) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body", [(503, None), (200, _body(active=False)), (200, _body(id="forged"))]
)
async def test_unavailable_or_invalid_identity_fails_closed(
    status: int, body: dict | None
) -> None:
    with pytest.raises(SessionUnavailable):
        await verify_tagent_session(
            session_cookie="opaque-session",
            cookie_name="__Host-tagent_session",
            base_url="http://127.0.0.1:5001",
            transport=_transport(status, body),
        )


@pytest.mark.asyncio
async def test_non_loopback_verifier_is_rejected() -> None:
    with pytest.raises(ValueError, match="loopback"):
        await verify_tagent_session(
            session_cookie="opaque-session",
            cookie_name="__Host-tagent_session",
            base_url="https://attacker.example",
        )


def test_database_record_token_binds_account_and_expires() -> None:
    token = sign_record_token(
        ACCOUNT_ID,
        namespace="open_notebook",
        database="open_notebook",
        secret="a" * 32,
    )
    encoded_claims = token.split(".")[1]
    import base64

    claims = json.loads(base64.urlsafe_b64decode(encoded_claims + "=="))
    assert claims["ID"] == account_record_id(ACCOUNT_ID)
    assert claims["AC"] == "tagent_account"
    assert claims["exp"] - claims["iat"] == 60
    with pytest.raises(ValueError, match="secret"):
        sign_record_token(
            ACCOUNT_ID,
            namespace="open_notebook",
            database="open_notebook",
            secret="weak",
        )
