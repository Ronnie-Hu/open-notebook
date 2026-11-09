"""Standalone SurrealDB v2 record-permission probe for TAgent integration design."""

from surrealdb import Surreal


def main() -> None:
    db = Surreal("mem://")
    db.connect()
    try:
        db.use("tagent_probe", "notebook_probe")
        db.query("DEFINE TABLE account SCHEMALESS")
        db.query(
            "DEFINE ACCESS tenant ON DATABASE TYPE RECORD "
            "SIGNIN (SELECT * FROM account WHERE email = $email "
            "AND crypto::argon2::compare(pass, $pass)) "
            "DURATION FOR TOKEN 1h, FOR SESSION 1h"
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
                {"name": name, "email": f"{name}@example.test", "pass": "probe-only-pass"},
            )
            db.query(
                "CREATE type::thing('notebook', $name) SET owner = "
                "type::thing('account', $name), title = $title",
                {"name": name, "title": f"{name} private"},
            )
        accounts = db.query("SELECT email FROM account ORDER BY email")
        assert [row["email"] for row in accounts] == [
            "one@example.test", "two@example.test"
        ], accounts
        verified = db.query(
            "SELECT email, crypto::argon2::compare(pass, $pass) AS valid FROM account",
            {"pass": "probe-only-pass"},
        )
        assert all(row["valid"] for row in verified), verified
        for name in ("one", "two"):
            db.signin({
                "namespace": "tagent_probe", "database": "notebook_probe",
                "access": "tenant", "variables": {
                    "email": f"{name}@example.test", "pass": "probe-only-pass"
                },
            })
            rows = db.query("SELECT title FROM notebook ORDER BY title")
            assert [row["title"] for row in rows] == [f"{name} private"], rows
            assert [row["title"] for row in db.query("RETURN fn::all_notebooks()")
                    ] == [f"{name} private"]
            assert db.query("SELECT * FROM type::thing('notebook', $other)",
                            {"other": "two" if name == "one" else "one"}) == []
            if name == "one":
                assert db.query(
                    "UPDATE type::thing('notebook', 'two') SET title = 'tampered'"
                ) == []
                assert db.query(
                    "CREATE notebook SET owner = account:two, title = 'forged'"
                ) == []
        print("SurrealDB v2 record permissions isolated both notebooks")
    finally:
        db.close()


if __name__ == "__main__":
    main()
