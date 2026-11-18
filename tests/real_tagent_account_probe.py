"""Opt-in local full-stack account probe using the real TAgent auth fixture.

Start ``tools.auth_integration_fixture`` from the TAgent checkout and a local
SurrealDB 2 instance first. This probe creates a unique Surreal namespace and
registers throwaway accounts in the fixture's in-memory identity database.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx

NOTEBOOK_ORIGIN = "https://tagent.example.org:8502"
TAGENT_FIXTURE = "http://127.0.0.1:18801"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def register_and_login(client: httpx.AsyncClient, email: str) -> tuple[str, str]:
    password = f"temporary-test-password-{uuid4().hex}"
    origin = {"Origin": NOTEBOOK_ORIGIN}
    registered = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "name": "Test Student"},
        headers=origin,
    )
    assert registered.status_code == 201, registered.text
    logged_in = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=origin,
    )
    assert logged_in.status_code == 200, logged_in.text
    session = client.cookies.get("__Host-tagent_session")
    assert session, "TAgent did not issue its test session cookie"
    account = logged_in.json()["user"]["id"]
    return account, password


def notebook_headers(
    client: httpx.AsyncClient, csrf: str | None = None
) -> dict[str, str]:
    session = client.cookies.get("__Host-tagent_session")
    assert session
    headers = {"Cookie": f"__Host-tagent_session={session}"}
    if csrf:
        headers.update({"Origin": NOTEBOOK_ORIGIN, "X-Tagent-CSRF": csrf})
    return headers


async def main() -> None:
    if os.getenv("RUN_REAL_TAGENT_ACCOUNT_PROBE") != "1":
        raise RuntimeError("Explicit local integration-test opt-in is required")
    surreal_url = os.environ["SURREAL2_TENANT_PROBE_URL"]
    surreal_password = os.environ["SURREAL2_TENANT_PROBE_PASSWORD"]
    if surreal_url != "ws://127.0.0.1:18765/rpc":
        raise ValueError("Probe requires the dedicated loopback SurrealDB port")

    async with (
        httpx.AsyncClient(base_url=TAGENT_FIXTURE) as alice,
        httpx.AsyncClient(base_url=TAGENT_FIXTURE) as bob,
        httpx.AsyncClient(base_url=TAGENT_FIXTURE) as second_device,
    ):
        marker = await alice.get("/__integration_fixture")
        assert marker.status_code == 200 and marker.json() == {
            "fixture": "in_memory_tagent_auth"
        }, "The target is not the dedicated TAgent test fixture"

        unique = uuid4().hex
        alice_email = f"alice-{unique}@example.org"
        bob_email = f"bob-{unique}@example.org"
        alice_id, alice_password = await register_and_login(alice, alice_email)
        bob_id, _ = await register_and_login(bob, bob_email)
        assert alice_id != bob_id
        alice_me = await alice.get("/auth/me", headers=notebook_headers(alice))
        bob_me = await bob.get("/auth/me", headers=notebook_headers(bob))
        assert alice_me.status_code == bob_me.status_code == 200
        alice_csrf = alice_me.json()["csrf"]
        bob_csrf = bob_me.json()["csrf"]

        second_login = await second_device.post(
            "/auth/login",
            json={"email": alice_email, "password": alice_password},
            headers={"Origin": NOTEBOOK_ORIGIN},
        )
        assert second_login.status_code == 200, second_login.text
        assert second_login.json()["user"]["id"] == alice_id

        os.environ.update(
            {
                "SURREAL_URL": surreal_url,
                "SURREAL_USER": "root",
                "SURREAL_PASSWORD": surreal_password,
                "SURREAL_NAMESPACE": f"notebook_real_auth_{unique}",
                "SURREAL_DATABASE": "notebook",
                "OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE": "1",
                "OPEN_NOTEBOOK_LEGACY_ADMIN_ID": str(uuid4()),
                "OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY": "a" * 48,
                "OPEN_NOTEBOOK_ENCRYPTION_KEY": "b" * 48,
                "OPEN_NOTEBOOK_TAGENT_LIFECYCLE_KEY": "l" * 48,
                "OPEN_NOTEBOOK_PUBLIC_ORIGIN": NOTEBOOK_ORIGIN,
                "OPEN_NOTEBOOK_TAGENT_VERIFIER_URL": TAGENT_FIXTURE,
            }
        )
        from api import main as notebook_api
        from api.routers import config as config_router

        async def no_version_check(_version):
            return None, False

        config_router.get_latest_version_cached = no_version_check
        async with notebook_api.lifespan(notebook_api.app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=notebook_api.app),
                base_url=NOTEBOOK_ORIGIN,
            ) as notebook:
                anonymous = await notebook.get("/api/notebooks")
                assert anonymous.status_code == 401, anonymous.text
                created = await notebook.post(
                    "/api/notebooks",
                    json={"name": "Alice's private notebook"},
                    headers=notebook_headers(alice, alice_csrf),
                )
                assert created.status_code == 200, created.text
                notebook_id = created.json()["id"]

                second_view = await notebook.get(
                    "/api/notebooks", headers=notebook_headers(second_device)
                )
                assert second_view.status_code == 200, second_view.text
                assert [item["id"] for item in second_view.json()] == [notebook_id]

                bob_view = await notebook.get(
                    "/api/notebooks", headers=notebook_headers(bob)
                )
                assert bob_view.status_code == 200 and bob_view.json() == [], (
                    bob_view.text
                )
                bob_created = await notebook.post(
                    "/api/notebooks",
                    json={"name": "Bob's private notebook"},
                    headers=notebook_headers(bob, bob_csrf),
                )
                assert bob_created.status_code == 200, bob_created.text
                bob_notebook_id = bob_created.json()["id"]
                alice_view = await notebook.get(
                    "/api/notebooks", headers=notebook_headers(alice)
                )
                assert [item["id"] for item in alice_view.json()] == [notebook_id]
                bob_view = await notebook.get(
                    "/api/notebooks", headers=notebook_headers(bob)
                )
                assert [item["id"] for item in bob_view.json()] == [bob_notebook_id]
                guessed = await notebook.get(
                    f"/api/notebooks/{notebook_id}", headers=notebook_headers(bob)
                )
                assert guessed.status_code == 404, guessed.text

                note = await notebook.post(
                    "/api/notes",
                    json={
                        "title": "Alice's note",
                        "content": "Only Alice can read this",
                        "notebook_id": notebook_id,
                    },
                    headers=notebook_headers(alice, alice_csrf),
                )
                assert note.status_code == 200, note.text
                note_id = note.json()["id"]
                alice_note = await notebook.get(
                    f"/api/notes/{note_id}", headers=notebook_headers(second_device)
                )
                assert alice_note.status_code == 200, alice_note.text
                assert alice_note.json()["content"] == "Only Alice can read this"
                bob_note = await notebook.get(
                    f"/api/notes/{note_id}", headers=notebook_headers(bob)
                )
                assert bob_note.status_code == 404, bob_note.text

                credential = await notebook.post(
                    "/api/credentials",
                    json={
                        "name": "Alice's model key",
                        "provider": "openai",
                        "modalities": ["language"],
                        "api_key": "local-probe-only-key",
                    },
                    headers=notebook_headers(alice, alice_csrf),
                )
                assert credential.status_code == 201, credential.text
                bob_keys = await notebook.get(
                    "/api/credentials", headers=notebook_headers(bob)
                )
                assert bob_keys.status_code == 200 and bob_keys.json() == [], (
                    bob_keys.text
                )

    print("PASS: real registration, sessions, second device and notebook isolation")


if __name__ == "__main__":
    asyncio.run(main())
