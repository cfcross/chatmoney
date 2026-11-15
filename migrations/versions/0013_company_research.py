"""Add approved website sources, scoped read grants, research reports and private lead pointers."""

from alembic import op
import sqlalchemy as sa
from app.research.tables_v1 import define

revision = "0013_company_research"
down_revision = "0012_customer_practice"
branch_labels = depends_on = None


def tables():
    m = sa.MetaData()
    for n in ("identities", "agents", "practice_sessions"):
        sa.Table(
            n,
            m,
            sa.Column("tenant", sa.Text, primary_key=True),
            sa.Column("id", sa.Text, primary_key=True),
        )
    return define(m)


def upgrade():
    for t in tables():
        t.create(op.get_bind())
        n = t.name
        op.execute(f"ALTER TABLE {n} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY research_tenant ON {n} USING (tenant=current_setting('app.tenant',true)) WITH CHECK (tenant=current_setting('app.tenant',true))"
        )
        op.execute(
            f"""DO $$ DECLARE r record; BEGIN FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants WHERE table_schema='public' AND table_name='agents' AND privilege_type='INSERT' LOOP EXECUTE format('GRANT SELECT, INSERT, UPDATE ON {n} TO %I',r.grantee); END LOOP; END $$"""
        )


def downgrade():
    op.execute(
        "DO $$ BEGIN IF EXISTS(SELECT FROM research_sources) THEN RAISE EXCEPTION 'Archive research records before downgrade'; END IF; END $$"
    )
    for t in reversed(tables()):
        t.drop(op.get_bind())
