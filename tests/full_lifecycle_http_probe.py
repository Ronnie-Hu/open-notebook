"""Opt-in local HTTP probe for TAgent + full OpenNotebook export and deletion.

Requires the dedicated loopback SurrealDB test instance, the disposable TAgent
auth fixture on 18801, and this branch's API on 18802. Never targets a public
server. Use a unique in-memory SurrealDB namespace for the API process.
"""

from __future__ import annotations

import json
import os
from io import BytesIO
from uuid import UUID, uuid4
from zipfile import ZipFile

import httpx

TAGENT = "http://127.0.0.1:18801"
NOTEBOOK = "http://127.0.0.1:18802"
PUBLIC_ORIGIN = "https://tagent.example.org"
NOTEBOOK_ORIGIN = "https://tagent.example.org:8502"


def _login(client: httpx.Client, email: str) -> tuple[str, str, str, str]:
    password = f"disposable-local-password-{uuid4().hex}"
    registered = client.post(
        "/auth/register",
        json={"email": email, "password": password, "name": "Local Probe"},
        headers={"Origin": NOTEBOOK_ORIGIN},
    )
    assert registered.status_code == 201, registered.text
    logged = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": NOTEBOOK_ORIGIN},
    )
    assert logged.status_code == 200, logged.text
    cookie = client.cookies.get("__Host-tagent_session")
    assert cookie
    return logged.json()["user"]["id"], logged.json()["csrf"], cookie, password


def _notebook_headers(cookie: str, csrf: str | None = None) -> dict[str, str]:
    headers = {"Cookie": f"__Host-tagent_session={cookie}"}
    if csrf:
        headers.update({"Origin": NOTEBOOK_ORIGIN, "X-Tagent-CSRF": csrf})
    return headers


def main() -> None:
    if os.getenv("RUN_FULL_LIFECYCLE_HTTP_PROBE") != "1":
        raise RuntimeError("Explicit local integration-test opt-in is required")
    with (
        httpx.Client(base_url=TAGENT, timeout=30, trust_env=False) as tagent,
        httpx.Client(base_url=NOTEBOOK, timeout=30, trust_env=False) as notebook,
    ):
        marker = tagent.get("/__integration_fixture")
        assert marker.status_code == 200 and marker.json() == {
            "fixture": "in_memory_tagent_auth"
        }
        health = notebook.get("/health")
        assert health.status_code == 200, health.text

        suffix = uuid4().hex
        alice, alice_csrf, alice_cookie, alice_password = _login(
            tagent, f"alice-{suffix}@example.org"
        )
        bob, bob_csrf, bob_cookie, _ = _login(tagent, f"bob-{suffix}@example.org")
        assert alice != bob
        own = notebook.post(
            "/api/notebooks",
            json={"name": "Alice private notebook"},
            headers=_notebook_headers(alice_cookie, alice_csrf),
        )
        assert own.status_code == 200, own.text
        note = notebook.post(
            "/api/notes",
            json={
                "title": "Alice note",
                "content": "Alice private body",
                "notebook_id": own.json()["id"],
            },
            headers=_notebook_headers(alice_cookie, alice_csrf),
        )
        assert note.status_code == 200, note.text
        upload_bytes = b"private upload for Alice only\n"
        upload = notebook.post(
            "/api/sources",
            data={
                "type": "upload",
                "notebooks": json.dumps([own.json()["id"]]),
                "async_processing": "true",
            },
            files={"file": ("private.txt", upload_bytes, "text/plain")},
            headers=_notebook_headers(alice_cookie, alice_csrf),
        )
        assert upload.status_code == 200, upload.text
        source_id = upload.json()["id"]
        own_download = notebook.get(
            f"/api/sources/{source_id}/download",
            headers=_notebook_headers(alice_cookie),
        )
        assert own_download.status_code == 200 and own_download.content == upload_bytes
        other_download = notebook.get(
            f"/api/sources/{source_id}/download",
            headers=_notebook_headers(bob_cookie),
        )
        assert other_download.status_code in {403, 404}, other_download.text
        other = notebook.post(
            "/api/notebooks",
            json={"name": "Bob private notebook"},
            headers=_notebook_headers(bob_cookie, bob_csrf),
        )
        assert other.status_code == 200, other.text

        exported = tagent.get(
            "/me/export",
            headers={"Cookie": f"__Host-tagent_session={alice_cookie}"},
        )
        assert exported.status_code == 200, exported.text[:200]
        with ZipFile(BytesIO(exported.content)) as archive:
            tagent_data = json.loads(archive.read("tagent-personal-data.json"))
            assert tagent_data["account"]["id"] == alice
            with ZipFile(BytesIO(archive.read("open-notebook.zip"))) as personal:
                notes = personal.read("records/note.json").decode("utf-8")
                assert "Alice private body" in notes
                assert "Bob private notebook" not in str(personal.namelist())
                upload_names = [
                    name
                    for name in personal.namelist()
                    if name.startswith(f"uploads/{UUID(alice).hex}/")
                ]
                assert len(upload_names) == 1, upload_names
                assert personal.read(upload_names[0]) == upload_bytes

        deleted = tagent.request(
            "DELETE",
            "/me/account",
            json={"password": alice_password},
            headers={
                "Cookie": f"__Host-tagent_session={alice_cookie}",
                "Origin": PUBLIC_ORIGIN,
                "X-Tagent-CSRF": alice_csrf,
            },
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"deleted": True}
        assert (
            tagent.get(
                "/auth/me", headers={"Cookie": f"__Host-tagent_session={alice_cookie}"}
            ).status_code
            == 401
        )
        assert (
            notebook.get(
                "/api/notebooks", headers=_notebook_headers(alice_cookie)
            ).status_code
            == 401
        )
        bob_notebooks = notebook.get(
            "/api/notebooks", headers=_notebook_headers(bob_cookie)
        )
        assert bob_notebooks.status_code == 200, bob_notebooks.text
        assert [item["id"] for item in bob_notebooks.json()] == [other.json()["id"]]

    print(
        "PASS: real HTTP registration, notebook and upload isolation, "
        "complete export, deletion and other-account retention"
    )


if __name__ == "__main__":
    main()
