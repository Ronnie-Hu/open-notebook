"""Student model endpoints must not reach the server's private network."""

import socket
from uuid import UUID

import pytest
from fastapi import HTTPException

from api.models import CreateCredentialRequest, UpdateCredentialRequest
from api.routers.credentials import create_credential, update_credential
from open_notebook.database.tenant_access import account_scope
from open_notebook.domain.credential import Credential
from open_notebook.exceptions import ConfigurationError
from open_notebook.graphs.source import validate_content_state
from open_notebook.utils.url_validation import (
    prepare_pinned_http_target,
    validate_model_endpoint,
)

ACCOUNT = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.test/v1",
        "https://127.0.0.1:5001/v1",
        "https://10.0.0.2/v1",
        "https://[::1]/v1",
        "https://user:pass@8.8.8.8/v1",
    ],
)
async def test_student_rejects_insecure_and_private_urls(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    with account_scope(ACCOUNT):
        with pytest.raises(ValueError):
            await validate_model_endpoint(url, "openai_compatible")


@pytest.mark.asyncio
async def test_student_accepts_public_https_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    with account_scope(ACCOUNT):
        await validate_model_endpoint("https://8.8.8.8/v1", "openai_compatible")


@pytest.mark.asyncio
async def test_student_rejects_mixed_dns_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.2", 443)),
        ],
    )
    with account_scope(ACCOUNT):
        with pytest.raises(ValueError, match="private addresses"):
            await validate_model_endpoint(
                "https://model.example.test/v1", "openai_compatible"
            )


@pytest.mark.asyncio
async def test_pinned_client_rechecks_dns_before_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    answers = iter(["8.8.8.8", "10.0.0.2"])

    def resolve(*_: object) -> list:
        address = next(answers)
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with account_scope(ACCOUNT):
        with pytest.raises(ValueError, match="private addresses"):
            await prepare_pinned_http_target(
                "https://model.example.test/v1", "openai_compatible"
            )


@pytest.mark.asyncio
async def test_admin_keeps_self_hosted_model_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    with account_scope(ACCOUNT, role="admin"):
        await validate_model_endpoint("http://127.0.0.1:11434", "ollama")


@pytest.mark.asyncio
async def test_student_cannot_save_server_credential_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    create = CreateCredentialRequest(
        name="Vertex", provider="vertex", credentials_path="/etc/secret.json"
    )
    update = UpdateCredentialRequest(credentials_path="/etc/secret.json")
    with account_scope(ACCOUNT):
        with pytest.raises(HTTPException) as first:
            await create_credential(create)
        with pytest.raises(HTTPException) as second:
            await update_credential("credential:any", update)
    assert first.value.status_code == second.value.status_code == 400


def test_existing_server_credential_path_cannot_be_used_by_student(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    credential = Credential(
        name="Vertex", provider="vertex", credentials_path="/etc/secret.json"
    )
    with account_scope(ACCOUNT):
        with pytest.raises(ConfigurationError, match="file paths"):
            credential.to_esperanto_config()
    with account_scope(ACCOUNT, role="admin"):
        assert (
            credential.to_esperanto_config()["credentials_path"] == "/etc/secret.json"
        )


@pytest.mark.asyncio
async def test_student_source_worker_rechecks_external_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    with account_scope(ACCOUNT):
        with pytest.raises(ValueError, match="external HTTPS"):
            await validate_content_state({"url": "http://127.0.0.1:5001/"})
