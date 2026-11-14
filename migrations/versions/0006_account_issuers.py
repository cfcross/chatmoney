"""Allow an enterprise administrator to delegate issuance of ordinary member accounts."""

import sqlalchemy as sa
from alembic import op

revision = "0006_account_issuers"
down_revision = "0005_workspace_login"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "web_accounts",
        sa.Column(
            "can_issue_accounts",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column("web_accounts", "can_issue_accounts")
