"""Create only a separate, explicitly named acceptance database using a local migration login."""

import os
import subprocess
import sys
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def main():
    owner = make_url(os.environ["MIGRATION_DATABASE_URL"])
    runtime = make_url(os.environ["DATABASE_URL"])
    if (
        not owner.database
        or not owner.database.endswith("_test")
        or owner.database != runtime.database
    ):
        raise SystemExit(
            "Both database URLs must target the same dedicated *_test database."
        )
    maintenance = owner.set(
        drivername="postgresql", database="postgres"
    ).render_as_string(hide_password=False)
    with psycopg.connect(maintenance, autocommit=True) as conn:
        if not conn.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (owner.database,)
        ).fetchone():
            conn.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(owner.database))
            )
    subprocess.run([sys.executable, "-m", "scripts.bootstrap"], check=True)


if __name__ == "__main__":
    main()
