"""Browser credentials and revocable sessions for existing business identities."""

from alembic import op
import sqlalchemy as sa

revision = "0005_workspace_login"
down_revision = "0004_model_connections"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "web_accounts",
        sa.Column("tenant", sa.Text, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("username", sa.Text, nullable=False),
        sa.Column("password_hash", sa.Text),
        sa.Column("setup_hash", sa.Text),
        sa.Column("setup_until", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("username"),
        sa.ForeignKeyConstraint(
            ["tenant", "id"], ["identities.tenant", "identities.id"]
        ),
    )
    op.create_table(
        "web_sessions",
        sa.Column("tenant", sa.Text, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("expires", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant", "actor"], ["web_accounts.tenant", "web_accounts.id"]
        ),
    )
    op.create_index("ix_web_sessions_account", "web_sessions", ["tenant", "actor"])
    for name in ["web_accounts", "web_sessions"]:
        op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_rows ON {name}
            USING (tenant=current_setting('app.tenant',true))
            WITH CHECK (tenant=current_setting('app.tenant',true))""")
    op.execute("""CREATE POLICY login_name ON web_accounts FOR SELECT
        USING (username=current_setting('app.login_name',true))""")
    op.execute("""CREATE POLICY lookup_session ON web_sessions FOR SELECT
        USING (id=current_setting('app.web_session',true))""")
    op.create_table(
        "web_login_limits",
        sa.Column("bucket", sa.Text, primary_key=True),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_web_login_limits_until", "web_login_limits", ["until"])


def downgrade():
    for name in ("web_login_limits", "web_sessions", "web_accounts"):
        op.drop_table(name)
