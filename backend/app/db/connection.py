from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from app.core.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        str(settings.database_url), pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
