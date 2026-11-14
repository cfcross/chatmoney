"""Keep each member's assistants attached to their stable workbench identity."""

from alembic import op
import sqlalchemy as sa

revision = "0007_agent_owner"
down_revision = "0006_account_issuers"
branch_labels = depends_on = None


def upgrade():
    op.add_column("agents", sa.Column("owner_id", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_agents_tenant_owner_id",
        "agents",
        "identities",
        ["tenant", "owner_id"],
        ["tenant", "id"],
    )
    op.create_index("ix_agents_owner", "agents", ["tenant", "owner_id"])


def downgrade():
    op.drop_index("ix_agents_owner", table_name="agents")
    op.drop_constraint("fk_agents_tenant_owner_id", "agents", type_="foreignkey")
    op.drop_column("agents", "owner_id")
