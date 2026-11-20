import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from bcb_pipeline.load import get_engine


@pytest.fixture(scope="session")
def engine():
    eng = get_engine()
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("PostgreSQL not reachable: run `docker compose up -d` first")
    yield eng
    eng.dispose()


@pytest.fixture
def conn(engine):
    """A connection inside a transaction that is always rolled back.

    Tests can write freely; nothing is left in the database afterwards.
    """
    connection = engine.connect()
    transaction = connection.begin()
    yield connection
    transaction.rollback()
    connection.close()
