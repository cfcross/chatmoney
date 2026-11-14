"""Keep existing credentials with their creator and enforce account-private access."""

from alembic import op
import sqlalchemy as sa


revision = "0009_personal_model_connections"
down_revision = "0008_workspace_management"
branch_labels = depends_on = None


def upgrade():
    tab = "model_connections"
    op.drop_constraint("uq_model_connections_tenant_name", tab, type_="unique")
    op.create_unique_constraint(
        "uq_model_connections_tenant_created_by_name",
        tab,
        ["tenant", "created_by", "name"],
    )
    # Unknown creators in old installations stay inaccessible. New rows must have a real owner.
    op.execute("""ALTER TABLE model_connections ADD CONSTRAINT fk_model_connections_tenant_created_by
        FOREIGN KEY (tenant,created_by) REFERENCES identities(tenant,id) NOT VALID""")
    db = op.get_bind()
    unknown = db.scalar(
        sa.text("""SELECT EXISTS (SELECT 1 FROM model_connections m
        WHERE NOT EXISTS (SELECT 1 FROM identities i WHERE (i.tenant,i.id)=(m.tenant,m.created_by)))""")
    )
    if not unknown:
        op.execute(
            "ALTER TABLE model_connections VALIDATE CONSTRAINT fk_model_connections_tenant_created_by"
        )
    # Only the original create response proves authorship; a publisher may be a different person.
    op.execute("""WITH creators AS (
        SELECT i.tenant,i.response->'data'->>'agent_id' AS aid,min(i.actor) AS actor
        FROM idempotency i JOIN audit_log a ON a.tenant=i.tenant AND a.actor=i.actor
            AND a.detail->>'request_id'=i.response->>'request_id'
        JOIN identities u ON u.tenant=i.tenant AND u.id=i.actor
        WHERE a.resource='/api/v1/agents' AND a.operation='POST' AND i.status=201
        GROUP BY i.tenant,i.response->'data'->>'agent_id' HAVING count(DISTINCT i.actor)=1
    ) UPDATE agents ag SET owner_id=g.actor FROM creators g
      WHERE ag.owner_id IS NULL AND ag.tenant=g.tenant AND ag.id=g.aid""")
    op.execute("""CREATE POLICY account_private ON model_connections AS RESTRICTIVE
        USING (created_by=current_setting('app.model_actor',true))
        WITH CHECK (created_by=current_setting('app.model_actor',true))""")


def downgrade():
    db = op.get_bind()
    if db.scalar(
        sa.text("""SELECT EXISTS (SELECT 1 FROM model_connections
        GROUP BY tenant,name HAVING count(*)>1)""")
    ):
        raise RuntimeError("同一企业有不同账号使用同名连接。降级前请各账号先重命名。")
    op.execute("DROP POLICY account_private ON model_connections")
    op.drop_constraint(
        "fk_model_connections_tenant_created_by",
        "model_connections",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_model_connections_tenant_created_by_name",
        "model_connections",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_model_connections_tenant_name", "model_connections", ["tenant", "name"]
    )
