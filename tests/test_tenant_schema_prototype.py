"""Check SurrealDB record permissions before applying them to every data table."""

# The embedded SDK exposes a broad dynamic SurrealQL result union.
# mypy: ignore-errors

from uuid import UUID

from surrealdb import Surreal

from open_notebook.database.tenant_access import sign_record_token


def test_explicit_owner_and_permissions_with_external_record_token() -> None:
    secret = "prototype-only-secret-at-least-32-bytes"
    namespace, database = "tenant_proto", "tenant_proto"
    admin = UUID("5fc55ced-5acd-4fc8-bf17-0eb8fe36a10f")
    student = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")
    db = Surreal("mem://")
    db.connect()
    try:
        db.use(namespace, database)
        db.query(
            "DEFINE TABLE notebook SCHEMAFULL; DEFINE FIELD name ON notebook TYPE string"
        )
        db.query("DEFINE TABLE account SCHEMALESS")
        for actor in (admin, student):
            db.query(
                "CREATE type::thing('account', $actor) SET active = true",
                {"actor": actor.hex},
            )
        db.query("CREATE notebook:old SET name = 'legacy'")
        db.query(
            "DEFINE ACCESS tagent_account ON DATABASE TYPE RECORD "
            "SIGNIN (SELECT * FROM account WHERE false) "
            f"WITH JWT ALGORITHM HS512 KEY '{secret}' "
            "DURATION FOR TOKEN 1h, FOR SESSION 1h"
        )
        db.query(
            "DEFINE FIELD owner ON notebook TYPE record<account>; "
            "UPDATE notebook:old SET owner = type::thing('account', $admin)",
            {"admin": admin.hex},
        )
        db.query(
            "DEFINE TABLE OVERWRITE notebook SCHEMAFULL PERMISSIONS "
            "FOR select, create, update, delete WHERE owner = $auth.id"
        )
        db.query(
            "DEFINE TABLE source SCHEMAFULL; DEFINE FIELD title ON source TYPE string"
        )
        db.query("DEFINE FIELD owner ON source TYPE record<account>")
        db.query(
            "DEFINE TABLE OVERWRITE source SCHEMAFULL PERMISSIONS "
            "FOR select, create, update, delete WHERE owner = $auth.id"
        )
        db.query(
            "CREATE source:legacy SET title = 'admin', "
            "owner = type::thing('account', $admin)",
            {"admin": admin.hex},
        )
        db.query(
            "DEFINE TABLE reference TYPE RELATION FROM source TO notebook "
            "PERMISSIONS FOR select, create, update, delete "
            "WHERE owner = $auth.id AND in.owner = $auth.id AND out.owner = $auth.id"
        )
        db.query("DEFINE FIELD owner ON reference TYPE record<account>")

        def authenticate(actor: UUID) -> None:
            db.authenticate(
                sign_record_token(
                    actor, namespace=namespace, database=database, secret=secret
                )
            )

        authenticate(student)
        assert db.query("SELECT * FROM notebook") == []
        created = db.query(
            "CREATE notebook SET name = 'student', owner = type::thing('account', $student)",
            {"student": student.hex},
        )
        assert isinstance(created, list) and len(created) == 1, created
        assert str(created[0]["owner"]) == f"account:{student.hex}"
        assert (
            db.query(
                "CREATE notebook SET name = 'forged', owner = type::thing('account', $admin)",
                {"admin": admin.hex},
            )
            == []
        )
        assert [row["name"] for row in db.query("SELECT * FROM notebook")] == [
            "student"
        ]
        denied_relation = db.query(
            "RELATE source:legacy->reference->$notebook "
            "SET owner = type::thing('account', $student)",
            {"notebook": created[0]["id"], "student": student.hex},
        )
        assert denied_relation == []
        authenticate(admin)
        assert [row["name"] for row in db.query("SELECT * FROM notebook")] == ["legacy"]
    finally:
        db.close()
