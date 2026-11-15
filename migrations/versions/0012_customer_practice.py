"""Add isolated practice records. No changes to existing agents or customer data."""

from alembic import op
import sqlalchemy as sa
from app.practice.tables_v1 import define

revision = "0012_customer_practice"
down_revision = "0011_agent_templates"
branch_labels = None
depends_on = None


def tables():
    meta = sa.MetaData()
    for name in ("agents", "identities"):
        sa.Table(
            name,
            meta,
            sa.Column("tenant", sa.Text, primary_key=True),
            sa.Column("id", sa.Text, primary_key=True),
        )
    return define(meta)


def upgrade():
    for tab in tables():
        tab.create(op.get_bind())
        name = tab.name
        op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY practice_tenant ON {name} USING (tenant=current_setting('app.tenant',true)) WITH CHECK (tenant=current_setting('app.tenant',true))"
        )
        op.execute(f"""DO $$ DECLARE r record; BEGIN
          FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants WHERE table_schema='public'
          AND table_name='agents' AND privilege_type='INSERT' LOOP
          EXECUTE format('GRANT SELECT, INSERT, UPDATE ON {name} TO %I',r.grantee); END LOOP; END $$""")


def downgrade():
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT FROM practice_sessions) THEN RAISE EXCEPTION 'Archive practice records before downgrade'; END IF; END $$"
    )
    for tab in reversed(tables()):
        tab.drop(op.get_bind())
