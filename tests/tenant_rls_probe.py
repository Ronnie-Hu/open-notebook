"""Standalone SurrealDB record-permission probe for TAgent integration design."""

# The SDK exposes a broad dynamic SurrealQL result union.
# mypy: ignore-errors

import uuid

from surrealdb import Surreal

from open_notebook.database.tenant_access import sign_record_token

SECRET = "probe-only-jwt-secret-at-least-32-bytes"
NAMESPACE = "tagent_probe"
DATABASE = "notebook_probe"


def actor_id(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"tagent-probe-{name}")


def signed_actor_token(name: str) -> str:
    return sign_record_token(
        actor_id(name), namespace=NAMESPACE, database=DATABASE, secret=SECRET
    )


def main() -> None:
    db = Surreal("mem://")
    db.connect()
    try:
        db.use(NAMESPACE, DATABASE)
        db.query("DEFINE TABLE account SCHEMALESS")
        db.query(
            "DEFINE ACCESS tagent_account ON DATABASE TYPE RECORD "
            "SIGNIN (SELECT * FROM account WHERE email = $email "
            "AND crypto::argon2::compare(pass, $pass)) "
            # SurrealDB v2 requires a literal KEY here; parameter binding fails.
            f"WITH JWT ALGORITHM HS512 KEY '{SECRET}' "
            "DURATION FOR TOKEN 1h, FOR SESSION 1h",
        )
        db.query(
            "DEFINE TABLE notebook SCHEMALESS PERMISSIONS "
            "FOR select, create, update, delete WHERE owner = $auth.id"
        )
        db.query(
            "DEFINE FUNCTION fn::all_notebooks() { RETURN SELECT title FROM notebook; } "
            "PERMISSIONS FULL"
        )
        for name in ("one", "two"):
            db.query(
                "CREATE type::thing('account', $name) SET email = $email, "
                "pass = crypto::argon2::generate($pass)",
                {
                    "name": actor_id(name).hex,
                    "email": f"{name}@example.test",
                    "pass": "probe-only-pass",
                },
            )
            db.query(
                "CREATE type::thing('notebook', $name) SET owner = "
                "type::thing('account', $owner), title = $title",
                {
                    "name": name,
                    "owner": actor_id(name).hex,
                    "title": f"{name} private",
                },
            )
        accounts = db.query("SELECT email FROM account ORDER BY email")
        assert [row["email"] for row in accounts] == [
            "one@example.test",
            "two@example.test",
        ], accounts
        verified = db.query(
            "SELECT email, crypto::argon2::compare(pass, $pass) AS valid FROM account",
            {"pass": "probe-only-pass"},
        )
        assert all(row["valid"] for row in verified), verified
        for name in ("one", "two"):
            db.authenticate(signed_actor_token(name))
            rows = db.query("SELECT title FROM notebook ORDER BY title")
            assert [row["title"] for row in rows] == [f"{name} private"], rows
            assert [row["title"] for row in db.query("RETURN fn::all_notebooks()")] == [
                f"{name} private"
            ]
            assert (
                db.query(
                    "SELECT * FROM type::thing('notebook', $other)",
                    {"other": "two" if name == "one" else "one"},
                )
                == []
            )
            if name == "one":
                assert (
                    db.query(
                        "UPDATE type::thing('notebook', 'two') SET title = 'tampered'"
                    )
                    == []
                )
                assert (
                    db.query(
                        "CREATE notebook SET owner = account:two, title = 'forged'"
                    )
                    == []
                )
        try:
            db.authenticate(signed_actor_token("one") + "tampered")
        except RuntimeError:
            pass
        else:
            raise AssertionError("a tampered actor token was accepted")
        print(f"{db.version()} record permissions isolated both notebooks")
    finally:
        db.close()


if __name__ == "__main__":
    main()
