"""Opt-in ASGI/SurrealDB 2 account flow probe on an isolated test server.

Set RUN_SURREAL2_TENANT_PROBE=1 and SURREAL2_TENANT_PROBE_URL/PASSWORD,
then run ``uv run --locked python tests/account_mode_http_probe.py``.
"""

import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4

import httpx

ADMIN = UUID("5fc55ced-5acd-4fc8-bf17-0eb8fe36a10f")
ALICE = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")
BOB = UUID("68f85046-484d-4d03-a0a8-04762f2be657")
ORIGIN = "https://tagent.example.org:8502"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class Identity(BaseHTTPRequestHandler):
    def do_GET(self):
        token = self.headers.get("Cookie", "").partition("=")[2]
        account = {"alice": ALICE, "bob": BOB}.get(token)
        if self.path != "/auth/me" or not account:
            self.send_response(401)
            self.end_headers()
            return
        payload = json.dumps(
            {
                "user": {
                    "id": str(account),
                    "role": "student",
                    "active": True,
                    "deletion_pending": False,
                },
                "csrf": f"csrf-{token}",
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


async def main():
    if os.getenv("RUN_SURREAL2_TENANT_PROBE") != "1":
        raise RuntimeError("Explicit test-server opt-in is required")
    database_url = os.environ["SURREAL2_TENANT_PROBE_URL"]
    database_password = os.environ["SURREAL2_TENANT_PROBE_PASSWORD"]
    server = ThreadingHTTPServer(("127.0.0.1", 0), Identity)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.environ.update(
        {
            "SURREAL_URL": database_url,
            "SURREAL_USER": "root",
            "SURREAL_PASSWORD": database_password,
            "SURREAL_NAMESPACE": f"notebook_http_probe_{uuid4().hex}",
            "SURREAL_DATABASE": "notebook",
            "OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE": "1",
            "OPEN_NOTEBOOK_LEGACY_ADMIN_ID": str(ADMIN),
            "OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY": "a" * 48,
            "OPEN_NOTEBOOK_ENCRYPTION_KEY": "b" * 48,
            "OPEN_NOTEBOOK_PUBLIC_ORIGIN": ORIGIN,
            "OPEN_NOTEBOOK_TAGENT_VERIFIER_URL": f"http://127.0.0.1:{server.server_port}",
        }
    )
    try:
        from surreal_commands import CommandInput, command
        from surreal_commands.core.service import command_service
        from surreal_commands.repository import repo_query as queue_repo_query
        from surrealdb import RecordID

        from api import main as api_main
        from api.routers import config as config_router
        from open_notebook.database.repository import repo_query
        from open_notebook.database.tenant_access import account_scope, current_account
        from open_notebook.database.tenant_commands import (
            get_command_status,
            submit_command,
            tenant_command_handler,
        )

        class ProbeInput(CommandInput):
            label: str

        @command("tenant_probe", app="open_notebook")
        @tenant_command_handler
        async def tenant_probe(input_data: ProbeInput) -> dict:
            notebooks = await repo_query("SELECT VALUE name FROM notebook")
            return {
                "account": str(current_account()),
                "label": input_data.label,
                "notebooks": notebooks,
            }

        async def no_version_check(_version):
            return None, False

        config_router.get_latest_version_cached = no_version_check

        async with api_main.lifespan(api_main.app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api_main.app),
                base_url=ORIGIN,
            ) as client:

                def headers(token, write=False):
                    result = {"Cookie": f"__Host-tagent_session={token}"}
                    if write:
                        result.update(
                            {"Origin": ORIGIN, "X-Tagent-CSRF": f"csrf-{token}"}
                        )
                    return result

                config = await client.get("/api/config")
                assert config.status_code == 200, config.text
                assert "dbStatus" not in config.json()
                created = await client.post(
                    "/api/notebooks",
                    json={"name": "Alice notebook"},
                    headers=headers("alice", True),
                )
                assert created.status_code == 200, created.text
                notebook_id = created.json()["id"]
                alice = await client.get("/api/notebooks", headers=headers("alice"))
                bob = await client.get("/api/notebooks", headers=headers("bob"))
                assert alice.status_code == bob.status_code == 200, (
                    alice.text,
                    bob.text,
                )
                assert len(alice.json()) == 1 and alice.json()[0]["id"] == notebook_id
                assert bob.json() == [], bob.text
                note = await client.post(
                    "/api/notes",
                    json={
                        "title": "Alice note",
                        "content": "private body",
                        "notebook_id": notebook_id,
                    },
                    headers=headers("alice", True),
                )
                assert note.status_code == 200, note.text
                note_id = note.json()["id"]
                bob_notes = await client.get("/api/notes", headers=headers("bob"))
                assert bob_notes.status_code == 200 and bob_notes.json() == [], (
                    bob_notes.text
                )
                guessed_note = await client.get(
                    f"/api/notes/{note_id}", headers=headers("bob")
                )
                assert guessed_note.status_code == 404, guessed_note.text
                credential = await client.post(
                    "/api/credentials",
                    json={
                        "name": "Alice key",
                        "provider": "openai",
                        "modalities": ["language"],
                        "api_key": "test-personal-key",
                    },
                    headers=headers("alice", True),
                )
                assert credential.status_code == 201, credential.text
                bob_credentials = await client.get(
                    "/api/credentials", headers=headers("bob")
                )
                assert (
                    bob_credentials.status_code == 200 and bob_credentials.json() == []
                ), bob_credentials.text
                guessed = await client.get(
                    f"/api/notebooks/{notebook_id}", headers=headers("bob")
                )
                assert guessed.status_code == 404, guessed.text
                with account_scope(ALICE):
                    job_id = str(
                        await asyncio.to_thread(
                            submit_command,
                            "open_notebook",
                            "tenant_probe",
                            {"label": "alice job"},
                        )
                    )
                queued = await queue_repo_query(
                    "SELECT * FROM $job_id",
                    {"job_id": RecordID.parse(job_id)},
                )
                assert len(queued) == 1, queued
                job = queued[0]
                await command_service.execute_command(
                    job_id,
                    "open_notebook.tenant_probe",
                    job["args"],
                    job["context"],
                )
                with account_scope(ALICE):
                    completed = await get_command_status(job_id)
                assert completed.status == "completed", completed.status
                assert completed.result is not None, completed.status
                assert completed.result["account"] == str(ALICE), completed.result
                assert completed.result["notebooks"] == ["Alice notebook"], (
                    completed.result
                )
                with account_scope(BOB):
                    try:
                        await get_command_status(job_id)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError("Bob could read Alice's job")
                print(
                    "PASS: startup, data isolation, queued worker identity and job status"
                )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
