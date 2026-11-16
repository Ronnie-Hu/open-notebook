"""Bind every account-mode API request to a verified TAgent session."""

import hmac
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from api.tagent_identity import (
    SessionUnavailable,
    TagentSession,
    _session_endpoint,
    verify_tagent_session,
)
from open_notebook.database.tenant_access import account_scope

SessionVerifier = Callable[..., Awaitable[TagentSession | None]]
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS = {"/health"}


def _session_cookie(headers: Headers, name: str) -> str:
    """Reject duplicate session cookies instead of guessing which one wins."""
    found = []
    for raw in headers.getlist("cookie"):
        for part in raw.split(";"):
            key, separator, value = part.strip().partition("=")
            if separator and key == name:
                found.append(value.strip())
    return found[0] if len(found) == 1 else ""


async def _reject(send: Send, status: int, message: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": message})


class TagentAccountMiddleware:
    """Use only the loopback identity service; never trust client account headers."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        verifier_url: str,
        cookie_name: str,
        public_origin: str,
        verifier: SessionVerifier = verify_tagent_session,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(public_origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("TAgent account mode requires an HTTPS public origin")
        if cookie_name != "__Host-tagent_session":
            raise ValueError("TAgent account mode requires the Secure host cookie")
        _session_endpoint(verifier_url)
        self.app = app
        self.verifier_url = verifier_url
        self.cookie_name = cookie_name
        self.public_origin = public_origin.rstrip("/")
        self.verifier = verifier
        self.transport = transport

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"].upper()
        path = scope["path"]
        if path in _PUBLIC_PATHS or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        cookie = _session_cookie(headers, self.cookie_name)
        if not cookie:
            await _reject(send, 401, b'{"detail":"Login required"}')
            return
        try:
            session = await self.verifier(
                session_cookie=cookie,
                cookie_name=self.cookie_name,
                base_url=self.verifier_url,
                transport=self.transport,
            )
        except (SessionUnavailable, httpx.RequestError):
            await _reject(send, 503, b'{"detail":"Identity service unavailable"}')
            return
        if session is None:
            await _reject(send, 401, b'{"detail":"Login required"}')
            return
        if method not in _SAFE_METHODS:
            origin = headers.get("origin", "").rstrip("/")
            csrf = headers.get("x-tagent-csrf", "")
            if (
                origin != self.public_origin
                or not csrf
                or not hmac.compare_digest(csrf, session.csrf)
            ):
                await _reject(
                    send, 403, b'{"detail":"Invalid request origin or CSRF token"}'
                )
                return

        with account_scope(session.account_id, role=session.role):
            await self.app(scope, receive, send)
