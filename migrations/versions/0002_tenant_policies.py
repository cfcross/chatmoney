"""Scope all business SQL to the authenticated tenant."""

from alembic import op

revision = "0002_tenant_policies"
down_revision = "1caaa18ee55d"
branch_labels = depends_on = None
tables = """identities products customers accounts conversations mock_orders source_records agents
agent_versions knowledge_bases documents knowledge_chunks knowledge_snapshots tasks sessions messages
memory memory_changes runs tool_executions model_calls send_ledger mock_receipts handoffs events
idempotency audit_log webhook_deliveries""".split()


def upgrade():
    for name in tables:
        op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f"""CREATE POLICY tenant_rows ON "{name}" USING
                    (tenant = current_setting('app.tenant',true))
                    WITH CHECK (tenant = current_setting('app.tenant',true))""")
    op.execute("""CREATE POLICY resolve_credential ON identities FOR SELECT USING
                  (token_hash = current_setting('app.credential_hash', true))""")


def downgrade():
    op.execute("DROP POLICY resolve_credential ON identities")
    for name in tables:
        op.execute(f'DROP POLICY tenant_rows ON "{name}"')
        op.execute(f'ALTER TABLE "{name}" DISABLE ROW LEVEL SECURITY')
