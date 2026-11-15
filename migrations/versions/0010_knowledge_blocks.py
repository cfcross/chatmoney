"""Store large UTF-8 sources in independently readable blocks; keep all legacy originals."""
from alembic import op
import sqlalchemy as sa

revision = "0010_knowledge_blocks"
down_revision = "0009_personal_model_connections"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("documents", sa.Column("original_name", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("source_sha256", sa.Text(), nullable=True))
    op.create_table(
        "knowledge_source_blocks",
        sa.Column("tenant", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("tenant", "id"),
        sa.ForeignKeyConstraint(["tenant", "document_id"], ["documents.tenant", "documents.id"]),
        sa.UniqueConstraint("tenant", "document_id", "ordinal"),
    )
    op.execute("ALTER TABLE knowledge_source_blocks ENABLE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_rows ON knowledge_source_blocks
        USING (tenant=current_setting('app.tenant',true))
        WITH CHECK (tenant=current_setting('app.tenant',true))""")
    # A later upgrade from a downgraded test schema must restore runtime grants too.
    op.execute("""DO $$ DECLARE r record; BEGIN
      FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
        WHERE table_schema='public' AND table_name='documents' AND privilege_type='INSERT'
      LOOP EXECUTE format('GRANT SELECT, INSERT, DELETE ON knowledge_source_blocks TO %I',r.grantee);
      END LOOP;
    END $$""")


def downgrade():
    # Refuse to discard non-inline source texts.
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM knowledge_source_blocks)
        THEN RAISE EXCEPTION 'Archive large source files before a storage downgrade'; END IF; END $$""")
    op.drop_table("knowledge_source_blocks")
    for column in ("source_sha256", "original_name", "size_bytes"):
        op.drop_column("documents", column)
