"""Tenant credentials and nullable references; existing profile snapshots stay untouched."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_model_connections"
down_revision = "0003_tenant_directory"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "model_connections",
        sa.Column("tenant", sa.Text, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False, server_default="deepseek"),
        sa.Column("encrypted_api_key", sa.Text, nullable=False),
        sa.Column("default_model", sa.Text, nullable=False),
        sa.Column("supported_models", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.Text),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column(
            "created",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant", "name"),
    )
    op.execute("ALTER TABLE model_connections ENABLE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_rows ON model_connections
        USING (tenant=current_setting('app.tenant',true))
        WITH CHECK (tenant=current_setting('app.tenant',true))""")
    for name, field in [("agents", "draft"), ("agent_versions", "config")]:
        op.add_column(name, sa.Column("model_connection_id", sa.Text))
        op.create_foreign_key(
            f"fk_{name}_model_connection",
            name,
            "model_connections",
            ["tenant", "model_connection_id"],
            ["tenant", "id"],
        )
        op.create_check_constraint(
            f"ck_{name}_model_binding",
            name,
            f"model_connection_id IS NOT DISTINCT FROM ({field}->>'model_connection_id')",
        )
        op.create_index(
            f"ix_{name}_model_binding", name, ["tenant", "model_connection_id"]
        )
    # Only the two known development administrators. Other administrators are explicitly provisioned.
    op.execute("""UPDATE identities SET roles=roles || '["model_admin"]'::jsonb
        WHERE (tenant,id) IN (('tob','tob-admin'),('toc','toc-admin'))
        AND roles @> '["config","publish"]'::jsonb AND NOT roles ? 'model_admin'""")


def downgrade():
    op.execute("""UPDATE identities SET roles=roles - 'model_admin'
        WHERE (tenant,id) IN (('tob','tob-admin'),('toc','toc-admin')) AND roles ? 'model_admin'""")
    for name in ["agents", "agent_versions"]:
        op.drop_index(f"ix_{name}_model_binding", table_name=name)
        op.drop_constraint(f"ck_{name}_model_binding", name)
        op.drop_constraint(f"fk_{name}_model_connection", name)
        op.drop_column(name, "model_connection_id")
    op.drop_table("model_connections")
