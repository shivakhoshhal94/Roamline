"""Migration plumbing only. No domain tables are required yet."""
from alembic import context
from app.core.config import Settings
from app.db.connection import create_db_engine

config = Settings()
target_metadata = None

if context.is_offline_mode():
    context.configure(url=str(config.database_url), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_db_engine(config)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
