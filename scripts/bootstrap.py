"""Apply schema using the migration identity. API and worker use a restricted login."""

import os
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
import psycopg
from psycopg import sql
from langgraph.checkpoint.postgres import PostgresSaver


def main():
    from app.schema import meta

    user_url = make_url(os.environ["DATABASE_URL"])
    owner_url = make_url(os.environ["MIGRATION_DATABASE_URL"])
    if user_url.username == owner_url.username:
        raise ValueError("Use a separate non-owner DATABASE_URL")
    owner = owner_url.set(drivername="postgresql").render_as_string(hide_password=False)
    role = sql.Identifier(user_url.username)
    with psycopg.connect(owner, autocommit=True) as db:
        if not db.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (user_url.username,)
        ).fetchone():
            db.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    role, sql.Literal(user_url.password)
                )
            )
        flags = db.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=%s",
            (user_url.username,),
        ).fetchone()
        if any(flags):
            raise ValueError(
                "Runtime account must not be a superuser or have BYPASSRLS"
            )
        db.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    command.upgrade(Config("alembic.ini"), "head")
    with PostgresSaver.from_conn_string(owner) as saver:
        saver.setup()
    with psycopg.connect(owner, autocommit=True) as db:
        db.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        db.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION public.worker_tenants() TO {}").format(
                role
            )
        )
        for table in meta.tables.values():
            name = sql.Identifier(table.name)
            access = (
                sql.SQL("SELECT")
                if table.name
                in {
                    "tenants",
                    "identities",
                    "mock_orders",
                    "products",
                    "customers",
                    "accounts",
                    "source_records",
                }
                else sql.SQL("SELECT, INSERT, UPDATE")
            )
            db.execute(sql.SQL("GRANT {} ON {} TO {}").format(access, name, role))
        db.execute(
            sql.SQL(
                "GRANT DELETE ON model_connections, web_accounts, web_sessions, web_login_limits, knowledge_source_blocks, agent_templates TO {}"
            ).format(role)
        )
        # New personal web identities can be inserted only in the authenticated tenant.
        # Existing roles, products and API credentials remain read-only to the runtime.
        db.execute(
            sql.SQL("GRANT INSERT, UPDATE (active) ON identities TO {}").format(role)
        )
        for name in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            tab = sql.Identifier(name)
            db.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(
                    tab, role
                )
            )
            db.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(tab))
            db.execute(
                sql.SQL("DROP POLICY IF EXISTS checkpoint_tenant ON {}").format(tab)
            )
            db.execute(
                sql.SQL(
                    "CREATE POLICY checkpoint_tenant ON {} USING (split_part(thread_id, ':', 1) = current_setting('app.tenant',true))"
                ).format(tab)
            )
        db.execute(
            sql.SQL(
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}"
            ).format(role)
        )
        # Tenant lock identifiers are allocated only when the tenant owner inserts a tenant.
        db.execute(sql.SQL("REVOKE ALL ON SEQUENCE tenants_upload_lock_key_seq FROM {}").format(role))
    print("Database migrations, checkpoints and runtime privileges ready.")


if __name__ == "__main__":
    main()
