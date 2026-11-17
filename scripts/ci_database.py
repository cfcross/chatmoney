"""Provision an empty runner-local database. The server bootstrap login ends at this step."""
import os
import secrets

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL


def prepare(port=55432):
    # This step is specific to the ephemeral service in ci.yml; an existing
    # database or role is an error and must never be adopted or cleared.
    host, db_name = "127.0.0.1", "chatmoney_test"
    roles = {"MIGRATION_DATABASE_URL": "cm_ci_migrate", "DATABASE_URL": "cm_ci_runtime"}
    credentials = {name: secrets.token_urlsafe(32) for name in roles.values()}
    with psycopg.connect(host=host, port=port, dbname="postgres", user="postgres", autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)).fetchone():
            raise RuntimeError("CI requires a fresh local PostgreSQL service")
        with conn.transaction():
            for role, password in credentials.items():
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {}")
                             .format(sql.Identifier(role), sql.Literal(password)))
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(db_name), sql.Identifier(roles["MIGRATION_DATABASE_URL"])))
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db_name)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(db_name), sql.Identifier(roles["DATABASE_URL"])))
        # Even the initial service credential cannot log in during migrations or pytest.
        conn.execute("ALTER ROLE postgres NOLOGIN")
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as out:
        for key, role in roles.items():
            password = credentials[role]
            print("::add-mask::" + password, flush=True)
            url = URL.create("postgresql+psycopg", username=role, password=password,
                             host=host, port=port, database=db_name)
            out.write(key + "=" + url.render_as_string(hide_password=False) + "\n")


if __name__ == "__main__":
    prepare()
