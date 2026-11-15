"""SQLAlchemy Core models. All business foreign keys include the tenant."""

from sqlalchemy import (
    LargeBinary,
    MetaData,
    Table,
    Column,
    String,
    Text,
    Integer,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    CheckConstraint,
    UniqueConstraint,
    Index,
    Identity,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

meta = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s",
        "pk": "pk_%(table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    }
)


def col(name, typ=Text, *, null=False, default=None):
    return Column(name, typ, nullable=null, server_default=default)


def ref(key, target):
    return ForeignKeyConstraint(
        ["tenant", key], ["%s.tenant" % target, "%s.id" % target]
    )


def table(name, *fields):
    return Table(
        name,
        meta,
        Column("tenant", Text, ForeignKey("tenants.id"), primary_key=True),
        Column("id", Text, primary_key=True),
        *fields,
    )


def instant(name):
    return col(name, DateTime(timezone=True), default=func.now())


def js(name, default="'{}'"):
    return col(name, JSONB, default=text(default))


tenants = Table(
    "tenants",
    meta,
    Column("id", Text, primary_key=True),
    col("name"),
    col("company_ref"),
)
identities = table(
    "identities",
    col("token_hash", String(64)),
    js("roles", "'[]'"),
    js("products", "'[]'"),
    col("active", Boolean, default="true"),
    UniqueConstraint("token_hash"),
)
# Human logins bind to the same tenant/actor used by API authorization and audit.
web_accounts = table(
    "web_accounts",
    col("username"),
    col("password_hash", null=True),
    col("setup_hash", null=True),
    col("setup_until", DateTime(timezone=True), null=True),
    col("can_issue_accounts", Boolean, default="false"),
    ref("id", "identities"),
    UniqueConstraint("username"),
)
web_sessions = table(
    "web_sessions",
    col("actor"),
    col("expires", DateTime(timezone=True)),
    ref("actor", "web_accounts"),
    Index("ix_web_sessions_account", "tenant", "actor"),
)
login_limits = Table(
    "web_login_limits",
    meta,
    Column("bucket", Text, primary_key=True),
    col("attempts", Integer),
    col("until", DateTime(timezone=True)),
    Index("ix_web_login_limits_until", "until"),
)
products = table("products", col("name"), col("version_ref"))
customers = table("customers", col("name"), js("profile"))
accounts = table(
    "accounts",
    col("product_id"),
    col("enabled", Boolean, default="true"),
    js("capabilities", "'[]'"),
    ref("product_id", "products"),
)
conversations = table(
    "conversations",
    col("customer_id"),
    col("account_id"),
    col("external_ref"),
    col("permitted", Boolean, default="false"),
    col("opted_out", Boolean, default="false"),
    col("window_until", DateTime(timezone=True), null=True),
    col("owner", default="auto"),
    col("epoch", Integer, default="1"),
    col("active_session", null=True),
    ref("customer_id", "customers"),
    ref("account_id", "accounts"),
    UniqueConstraint("tenant", "account_id", "external_ref"),
)
orders = table(
    "mock_orders",
    col("customer_id"),
    col("product_id"),
    col("shipping"),
    ref("customer_id", "customers"),
    ref("product_id", "products"),
)
leads = table(
    "source_records",
    col("customer_id"),
    col("product_id"),
    ref("customer_id", "customers"),
    ref("product_id", "products"),
)
model_connections = table(
    "model_connections",
    col("name"),
    col("provider", default="deepseek"),
    col("encrypted_api_key"),
    col("default_model"),
    js("supported_models", "'[]'"),
    col("status", default="pending"),
    col("revision", Integer, default="1"),
    col("last_tested_at", DateTime(timezone=True), null=True),
    col("last_error_code", null=True),
    col("created_by"),
    instant("created"),
    instant("updated"),
    ref("created_by", "identities"),
    UniqueConstraint("tenant", "created_by", "name"),
)
agent_templates = table(
    "agent_templates",
    col("owner_id"),
    ref("owner_id", "identities"),
    col("revision", Integer, default="1"),
    js("content"),
    instant("created"),
    instant("updated"),
    Index("ix_agent_templates_owner", "tenant", "owner_id"),
)
agents = table(
    "agents",
    col("template_origin", JSONB(none_as_null=True), null=True),
    col("deleted_at", DateTime(timezone=True), null=True),
    col("owner_id", null=True),
    ref("owner_id", "identities"),
    Index("ix_agents_owner", "tenant", "owner_id"),
    col("product_id"),
    col("workflow"),
    col("name"),
    js("draft"),
    col("model_connection_id", null=True),
    ForeignKeyConstraint(
        ["tenant", "model_connection_id"],
        ["model_connections.tenant", "model_connections.id"],
        name="fk_agents_model_connection",
    ),
    CheckConstraint(
        "model_connection_id IS NOT DISTINCT FROM (draft->>'model_connection_id')",
        name="ck_agents_model_binding",
    ),
    Index("ix_agents_model_binding", "tenant", "model_connection_id"),
    col("revision", Integer, default="1"),
    col("published", Integer, default="0"),
    col("disabled", Boolean, default="false"),
    ref("product_id", "products"),
)
versions = table(
    "agent_versions",
    col("agent_id"),
    col("number", Integer),
    col("name"),
    js("config"),
    col("model_connection_id", null=True),
    ForeignKeyConstraint(
        ["tenant", "model_connection_id"],
        ["model_connections.tenant", "model_connections.id"],
        name="fk_agent_versions_model_connection",
    ),
    CheckConstraint(
        "model_connection_id IS NOT DISTINCT FROM (config->>'model_connection_id')",
        name="ck_agent_versions_model_binding",
    ),
    Index("ix_agent_versions_model_binding", "tenant", "model_connection_id"),
    col("actor"),
    instant("created"),
    ref("agent_id", "agents"),
    UniqueConstraint("tenant", "agent_id", "number"),
)
kbs = table(
    "knowledge_bases",
    col("deleted_at", DateTime(timezone=True), null=True),
    col("edit_revision", Integer, default="1"),
    col("product_id"),
    col("name"),
    col("latest", Integer, default="0"),
    ref("product_id", "products"),
)
docs = table(
    "documents",
    col("file_id", null=True),
    col("is_current", Boolean, default="true"),
    ref("file_id", "documents"),
    col("kb_id"),
    col("title"),
    col("original"),
    col("size_bytes", BigInteger, null=True),
    col("original_name", null=True),
    col("source_sha256", null=True),
    col("source_note", null=True),
    col("revision", Integer, default="1"),
    col("status"),
    col("actor"),
    ref("kb_id", "knowledge_bases"),
)
Index(
    "ix_documents_current_file",
    docs.c.tenant,
    func.coalesce(docs.c.file_id, docs.c.id),
    unique=True,
    postgresql_where=docs.c.is_current,
)
chunks = table(
    "knowledge_chunks",
    col("document_id"),
    col("ordinal", Integer),
    col("body"),
    ref("document_id", "documents"),
    UniqueConstraint("tenant", "document_id", "ordinal"),
)
source_blocks = table(
    "knowledge_source_blocks", col("document_id"), col("ordinal", Integer),
    col("payload", LargeBinary), ref("document_id", "documents"),
    UniqueConstraint("tenant", "document_id", "ordinal"),
)
snapshots = table(
    "knowledge_snapshots",
    col("kb_id"),
    col("number", Integer),
    js("documents", "'[]'"),
    col("actor"),
    instant("created"),
    ref("kb_id", "knowledge_bases"),
    UniqueConstraint("tenant", "kb_id", "number"),
)
tasks = table(
    "tasks",
    col("deleted_at", DateTime(timezone=True), null=True),
    col("edit_revision", Integer, default="1"),
    col("display_name", null=True),
    col("note", null=True),
    col("agent_id"),
    col("agent_version", Integer),
    col("purpose"),
    col("external_ref", null=True),
    col("status"),
    col("reason", null=True),
    ref("agent_id", "agents"),
    ForeignKeyConstraint(
        ["tenant", "agent_id", "agent_version"],
        ["agent_versions.tenant", "agent_versions.agent_id", "agent_versions.number"],
    ),
)
sessions = table(
    "sessions",
    col("task_id"),
    col("conversation_id"),
    col("customer_id"),
    js("context"),
    col("status"),
    col("handoff_id", null=True),
    ref("task_id", "tasks"),
    ref("conversation_id", "conversations"),
    ref("customer_id", "customers"),
)
messages = table(
    "messages",
    col("session_id"),
    col("account_id"),
    col("external_id", null=True),
    col("kind"),
    col("body"),
    col("seq", BigInteger),
    col("run_id", null=True),
    col("handled", Boolean, default="false"),
    col("occurred", DateTime(timezone=True), null=True),
    instant("created"),
    ref("session_id", "sessions"),
    ref("account_id", "accounts"),
    UniqueConstraint("tenant", "account_id", "external_id"),
    UniqueConstraint("tenant", "session_id", "seq"),
)
memory = table(
    "memory",
    col("revision", Integer, default="1"),
    js("facts", "'{}'"),
    col("summary", default=""),
    js("pending", "'[]'"),
    ref("id", "sessions"),
)
history = table(
    "memory_changes",
    col("session_id"),
    col("revision", Integer),
    col("key"),
    col("old", JSONB, null=True),
    col("value", JSONB, null=True),
    col("source_ref"),
    col("actor"),
    col("reason"),
    instant("created"),
    ref("session_id", "sessions"),
    ref("source_ref", "messages"),
)
runs = table(
    "runs",
    col("product_id"),
    col("task_id", null=True),
    col("session_id", null=True),
    col("kind"),
    col("queue"),
    col("status"),
    js("payload"),
    col("result", JSONB, null=True),
    col("error", JSONB, null=True),
    instant("created"),
    instant("due"),
    col("attempt", Integer, default="0"),
    col("lease_token", null=True),
    col("lease_until", DateTime(timezone=True), null=True),
    Column("ordinal", BigInteger, Identity(), nullable=False),
    ref("product_id", "products"),
    ref("task_id", "tasks"),
    ref("session_id", "sessions"),
)
Index("ix_runs_due", runs.c.tenant, runs.c.queue, runs.c.status, runs.c.due)
Index("ix_runs_session", runs.c.tenant, runs.c.session_id, runs.c.ordinal)
tools = table(
    "tool_executions",
    col("run_id"),
    col("tool"),
    col("action_key"),
    col("status"),
    js("outcome"),
    instant("created"),
    ref("run_id", "runs"),
    UniqueConstraint("tenant", "run_id", "action_key"),
)
model_calls = table(
    "model_calls",
    col("run_id"),
    col("stage"),
    col("attempt", Integer),
    col("status"),
    js("metrics"),
    instant("created"),
    ref("run_id", "runs"),
)
sends = table(
    "send_ledger",
    col("conversation_id"),
    col("session_id"),
    col("message_id"),
    col("run_id"),
    col("actor"),
    col("epoch", Integer),
    col("status"),
    js("sources", "'[]'"),
    col("receipt", null=True),
    instant("created"),
    ref("session_id", "sessions"),
    ref("conversation_id", "conversations"),
    ref("message_id", "messages"),
    ref("run_id", "runs"),
    UniqueConstraint("tenant", "run_id"),
)
receipts = table(
    "mock_receipts",
    col("send_id"),
    col("reference"),
    instant("created"),
    ref("send_id", "send_ledger"),
    UniqueConstraint("tenant", "send_id"),
)
handoffs = table(
    "handoffs",
    col("session_id"),
    col("reason"),
    js("evidence", "'[]'"),
    col("owner", null=True),
    col("status"),
    col("note", null=True),
    instant("created"),
    ref("session_id", "sessions"),
)
events = table(
    "events",
    col("product_id"),
    col("type"),
    col("session_id", null=True),
    col("task_id", null=True),
    col("run_id", null=True),
    js("data"),
    instant("created"),
    col("ordinal", BigInteger),
    ref("product_id", "products"),
    UniqueConstraint("tenant", "ordinal"),
)
idempotency = table(
    "idempotency",
    col("actor"),
    col("fingerprint"),
    col("status", Integer),
    js("response"),
)
audit = table(
    "audit_log",
    col("actor"),
    col("operation"),
    col("resource"),
    js("detail"),
    instant("created"),
)
webhooks = table(
    "webhook_deliveries",
    col("event_id"),
    col("subscriber"),
    col("status"),
    col("attempt", Integer, default="0"),
    instant("due"),
    ref("event_id", "events"),
    UniqueConstraint("tenant", "event_id", "subscriber"),
)

from app.practice.tables_v1 import define as define_practice
practice, buyer_private, practice_turns, practice_calls, practice_reviews = define_practice(meta)

from app.research.tables_v1 import define as define_research
research_sources, research_grants, research_jobs, research_leads = define_research(meta)
