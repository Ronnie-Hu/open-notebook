"""Verify a TAgent browser session without trusting client-supplied account IDs."""

from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx


@dataclass(frozen=True)
class TagentSession:
    account_id: UUID
    role: str
    csrf: str


class SessionUnavailable(Exception):
    """The identity service could not be reached or returned an invalid reply."""


def _session_endpoint(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise ValueError("TAgent session verifier must be a loopback HTTP origin")
    return base_url.rstrip("/") + "/auth/me"


async def verify_tagent_session(
    *,
    session_cookie: str,
    cookie_name: str,
    base_url: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TagentSession | None:
    """Return the current actor, or None for an unauthenticated session.

    Only the named session cookie crosses to the local identity service. A 5xx,
    timeout, malformed reply, or inactive account must fail closed.
    """
    endpoint = _session_endpoint(base_url)
    if not session_cookie:
        return None
    if cookie_name not in {"__Host-tagent_session", "tagent_session_dev"}:
        raise ValueError("Unexpected TAgent session cookie name")
    async with httpx.AsyncClient(
        timeout=2.0,
        trust_env=False,
        transport=transport,
        cookies={cookie_name: session_cookie},
    ) as client:
        try:
            response = await client.get(endpoint)
        except httpx.RequestError as exc:
            raise SessionUnavailable("TAgent identity service unavailable") from exc
    if response.status_code == 401:
        return None
    if response.status_code != 200:
        raise SessionUnavailable("TAgent identity service rejected verification")
    try:
        payload = response.json()
        user = payload["user"]
        actor = TagentSession(
            account_id=UUID(user["id"]), role=user["role"], csrf=payload["csrf"]
        )
        if (
            actor.role not in {"admin", "student"}
            or not isinstance(actor.csrf, str)
            or not actor.csrf
            or user.get("active") is not True
            or user.get("deletion_pending") is True
        ):
            raise ValueError("Invalid account status")
        return actor
    except (KeyError, TypeError, ValueError) as exc:
        raise SessionUnavailable("TAgent identity reply invalid") from exc
