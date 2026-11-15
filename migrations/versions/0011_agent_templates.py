"""Private template library and immutable creation provenance on each assistant."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011_agent_templates"
down_revision = "0010_knowledge_blocks"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_templates",
        sa.Column("tenant", sa.Text(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content", JSONB(), nullable=False, server_default="{}"),
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
        sa.ForeignKeyConstraint(
            ["tenant", "owner_id"], ["identities.tenant", "identities.id"]
        ),
    )
    op.create_index(
        "ix_agent_templates_owner", "agent_templates", ["tenant", "owner_id"]
    )
    op.execute("ALTER TABLE agent_templates ENABLE ROW LEVEL SECURITY")
    scope = "tenant=current_setting('app.tenant',true) AND owner_id=current_setting('app.model_actor',true)"
    op.execute(
        f"CREATE POLICY personal_templates ON agent_templates USING ({scope}) WITH CHECK ({scope})"
    )
    op.execute("""DO $$ DECLARE r record; BEGIN
      FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
        WHERE table_schema='public' AND table_name='agents' AND privilege_type='INSERT'
      LOOP EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON agent_templates TO %I',r.grantee);
      END LOOP;
    END $$""")
    op.add_column("agents", sa.Column("template_origin", JSONB(), nullable=True))


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT FROM agent_templates) OR
        EXISTS (SELECT FROM agents WHERE template_origin IS NOT NULL)
        THEN RAISE EXCEPTION 'Archive assistant template data before downgrading'; END IF; END $$""")
    op.drop_column("agents", "template_origin")
    op.drop_table("agent_templates")
