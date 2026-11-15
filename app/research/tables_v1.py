"""Frozen schema for revision 0013. A lead is never a Customer, conversation or send intent."""

from sqlalchemy import (
    Column,
    Table,
    Text,
    Integer,
    DateTime,
    ForeignKeyConstraint,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB


def define(meta):
    def col(n, t=Text, nullable=False, default=None):
        return Column(n, t, nullable=nullable, server_default=default)

    def fk(k, t):
        return ForeignKeyConstraint(["tenant", k], [t + ".tenant", t + ".id"])

    def tab(n, *fields):
        return Table(
            n,
            meta,
            Column("tenant", Text, primary_key=True),
            Column("id", Text, primary_key=True),
            *fields,
        )

    j = JSONB(none_as_null=True)
    dt = DateTime(timezone=True)

    def created():
        return col("created", dt, default=func.now())

    src = tab(
        "research_sources",
        col("name"),
        col("base_url"),
        col("policy", j),
        col("actor"),
        col("status"),
        col("expires", dt),
        created(),
    )
    grants = tab(
        "research_grants",
        col("owner_id"),
        fk("owner_id", "identities"),
        col("agent_id"),
        fk("agent_id", "agents"),
        col("version", Integer),
        col("fingerprint"),
        col("practice_id"),
        fk("practice_id", "practice_sessions"),
        col("source_id"),
        fk("source_id", src.name),
        col("status"),
        col("decided_by", nullable=True),
        col("reason", nullable=True),
        created(),
        Index("ix_research_grant", "tenant", "agent_id", "version"),
    )
    jobs = tab(
        "company_research_jobs",
        col("owner_id"),
        fk("owner_id", "identities"),
        col("agent_id"),
        fk("agent_id", "agents"),
        col("version", Integer),
        col("product_id"),
        col("source_id"),
        fk("source_id", src.name),
        col("grant_id"),
        fk("grant_id", grants.name),
        col("target"),
        col("input", j),
        col("tool_version"),
        col("skill_version"),
        col("status"),
        col("events", j),
        col("capture", j, True),
        col("report", j, True),
        col("error", nullable=True),
        col("lease", nullable=True),
        col("lease_until", dt, True),
        col("expires", dt),
        created(),
        col("updated", dt, default=func.now()),
        Index("ix_research_jobs_owner", "tenant", "owner_id", "agent_id"),
        Index("ix_research_work", "tenant", "status", "created"),
        Index("ix_research_retention", "tenant", "expires"),
    )
    leads = tab(
        "research_followup_leads",
        col("owner_id"),
        col("product_id"),
        col("target"),
        col("job_id"),
        fk("job_id", jobs.name),
        col("source_id"),
        fk("source_id", src.name),
        col("status"),
        created(),
        UniqueConstraint("tenant", "owner_id", "product_id", "target"),
    )
    return src, grants, jobs, leads
