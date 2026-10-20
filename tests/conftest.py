"""Shared pytest fixtures: a read-only connection to the built database."""

import pytest

from src import config
from src.load import connect


@pytest.fixture(scope="session")
def con():
    if not config.DB_PATH.exists():
        pytest.exit("Database not found - run `python run_pipeline.py` first.", returncode=1)
    connection = connect(read_only=True)
    yield connection
    connection.close()


@pytest.fixture
def scalar(con):
    """Run a query and return its single value."""
    return lambda sql: con.execute(sql).fetchone()[0]
