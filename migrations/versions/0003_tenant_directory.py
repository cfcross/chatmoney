"""Keep company information scoped. Only the scheduler needs to enumerate tenant ids."""

from alembic import op

revision = "0003_tenant_directory"
down_revision = "0002_tenant_policies"
branch_labels = depends_on = None


def upgrade():
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY own_company ON tenants USING (id=current_setting('app.tenant',true))"""
    )
    op.execute("""CREATE FUNCTION public.worker_tenants() RETURNS SETOF text LANGUAGE sql STABLE
                SECURITY DEFINER SET search_path=pg_catalog
                AS 'SELECT id FROM public.tenants ORDER BY id'""")
    op.execute("REVOKE ALL ON FUNCTION public.worker_tenants() FROM PUBLIC")


def downgrade():
    op.execute("DROP FUNCTION public.worker_tenants()")
    op.execute("DROP POLICY own_company ON tenants")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
