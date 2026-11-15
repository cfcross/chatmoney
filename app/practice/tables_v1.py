"""Frozen table definitions shared by SQLAlchemy metadata and Alembic 0012."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB


def define(meta):
    def col(name, typ=Text, null=False, default=None):
        return Column(name, typ, nullable=null, server_default=default)

    def fk(key, target):
        return ForeignKeyConstraint(
            ["tenant", key], [target + ".tenant", target + ".id"]
        )

    def table(name, *fields):
        return Table(
            name,
            meta,
            Column("tenant", Text, primary_key=True),
            Column("id", Text, primary_key=True),
            *fields,
        )

    dt = DateTime(timezone=True)
    j = JSONB(none_as_null=True)
    sim = table(
        "practice_sessions",
        col("owner_id"),
        fk("owner_id", "identities"),
        col("agent_id"),
        fk("agent_id", "agents"),
        col("source"),
        col("source_version", Integer),
        col("fingerprint"),
        col("snapshot", j),
        col("persona"),
        col("setting", j),
        col("status"),
        col("mode"),
        col("max_turns", Integer),
        col("turns", Integer, default="0"),
        col("token_limit", Integer),
        col("usage", Integer, default="0"),
        col("accounted", Integer, default="0"),
        col("memory", j, default="{}"),
        col("revision", Integer, default="1"),
        col("lease", null=True),
        col("until", dt, True),
        col("stop_reason", null=True),
        col("created", dt, default=func.now()),
        col("updated", dt, default=func.now()),
        col("finished", dt, True),
        col("review", default="unread"),
        col("candidate", default="none"),
        Index("ix_practice_owner", "tenant", "owner_id", "agent_id"),
        Index("ix_practice_queue", "tenant", "status", "updated"),
    )
    hidden = table(
        "practice_buyer_private", fk("id", "practice_sessions"), col("facts", j)
    )

    def child(name, *fields):
        return table(name, col("sim_id"), fk("sim_id", "practice_sessions"), *fields)

    turns = child(
        "practice_turns",
        col("num", Integer),
        col("buyer", null=True),
        col("seller", null=True),
        col("action", null=True),
        col("reason", null=True),
        col("evidence", j, default="{}"),
        col("tools", j, default="{}"),
        col("quality", j, default="{}"),
        col("created", dt, default=func.now()),
        UniqueConstraint("tenant", "sim_id", "num"),
    )
    calls = child(
        "practice_model_calls",
        col("num", Integer),
        col("stage"),
        col("state"),
        col("reserved", Integer),
        col("charged", Integer, default="0"),
        col("metrics", j, default="{}"),
        col("cached", j, null=True),
        col("created", dt, default=func.now()),
        UniqueConstraint("tenant", "sim_id", "num", "stage"),
    )
    reviews = child(
        "practice_reviews",
        col("actor"),
        col("kind"),
        col("note"),
        col("fingerprint"),
        col("created", dt, default=func.now()),
    )
    return sim, hidden, turns, calls, reviews
