"""
Finyl-DCP — SQLAlchemy engine/session setup.

Tables live in a dedicated schema (settings.DB_SCHEMA) so the app can share a
hosted PostgreSQL server without touching other projects' tables. In
docker-compose the schema is simply "finyl_dcp" inside its own database.
"""
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=1800,
)


@event.listens_for(engine, "connect")
def _set_search_path(dbapi_conn, _):
    """Pin every connection to the app schema."""
    cur = dbapi_conn.cursor()
    cur.execute(f'SET search_path TO "{settings.DB_SCHEMA}", public')
    cur.close()


@event.listens_for(engine, "begin")
def _set_search_path_txn(conn):
    """
    Re-pin the schema at the start of EVERY transaction with SET LOCAL.
    Hosted PostgreSQL is often fronted by a transaction-mode pooler (PgBouncer),
    where session-level SETs from the connect hook do not reliably stick to the
    server connection actually executing a given transaction.
    """
    conn.exec_driver_sql(f'SET LOCAL search_path TO "{settings.DB_SCHEMA}", public')


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency yielding a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema():
    with engine.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.DB_SCHEMA}"'))
        conn.commit()
