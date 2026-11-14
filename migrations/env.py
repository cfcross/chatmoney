import os
from alembic import context
from sqlalchemy import create_engine
from app.schema import meta

with create_engine(
    os.environ.get("MIGRATION_DATABASE_URL") or os.environ["DATABASE_URL"],
    hide_parameters=True,
).begin() as conn:
    context.configure(connection=conn, target_metadata=meta)
    context.run_migrations()
