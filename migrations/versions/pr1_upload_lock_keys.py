"""Assign each tenant a stable, collision-free PostgreSQL upload lock namespace."""
from alembic import op
import sqlalchemy as sa

revision = "pr1_upload_lock_keys"
down_revision = "0013_company_research"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tenants", sa.Column("upload_lock_key", sa.Integer(),
                                       sa.Identity(always=True), nullable=False))
    op.create_unique_constraint("uq_tenants_upload_lock_key", "tenants", ["upload_lock_key"])


def downgrade():
    op.drop_column("tenants", "upload_lock_key")
