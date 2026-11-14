"""Archive managed resources and track immutable document revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0008_workspace_management"
down_revision = "0007_agent_owner"
branch_labels = depends_on = None


def upgrade():
    # Web-issued personal accounts have stable generated IDs. Preserve explicit service identities.
    op.execute("""UPDATE identities AS i SET roles = '["business","own_agents"]'::jsonb
        FROM web_accounts AS w
        WHERE w.tenant=i.tenant AND w.id=i.id AND i.id ~ '^member_[0-9a-f]{32}$'
          AND i.roles = '["business"]'::jsonb""")
    for table in ("agents", "tasks", "knowledge_bases"):
        op.add_column(
            table, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
        )
    for table in ("tasks", "knowledge_bases"):
        op.add_column(
            table,
            sa.Column(
                "edit_revision", sa.Integer(), nullable=False, server_default="1"
            ),
        )
    op.add_column("tasks", sa.Column("display_name", sa.Text()))
    op.add_column("tasks", sa.Column("note", sa.Text()))
    op.add_column("documents", sa.Column("file_id", sa.Text()))
    op.add_column(
        "documents",
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_foreign_key(
        "fk_documents_tenant_file_id",
        "documents",
        "documents",
        ["tenant", "file_id"],
        ["tenant", "id"],
    )
    op.create_index(
        "ix_documents_current_file",
        "documents",
        ["tenant", sa.text("COALESCE(file_id, id)")],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )


def downgrade():
    # Before this revision only the single-team workbench issued member-owned assistants.
    op.execute("""UPDATE identities AS i SET roles = '["business"]'::jsonb
        FROM web_accounts AS w
        WHERE w.tenant=i.tenant AND w.id=i.id AND i.tenant <> 'team'
          AND i.id ~ '^member_[0-9a-f]{32}$' AND i.roles = '["business","own_agents"]'::jsonb""")
    op.drop_index("ix_documents_current_file", table_name="documents")
    op.drop_constraint("fk_documents_tenant_file_id", "documents", type_="foreignkey")
    for name in ("is_current", "file_id"):
        op.drop_column("documents", name)
    for name in ("note", "display_name"):
        op.drop_column("tasks", name)
    for table in ("tasks", "knowledge_bases"):
        op.drop_column(table, "edit_revision")
    for table in ("agents", "tasks", "knowledge_bases"):
        op.drop_column(table, "deleted_at")
