from collections.abc import Iterator
from types import MappingProxyType

import duckdb
import pytest

from lakehouse_rules import Rule, silver_select_sql


def apply_rules(
    con: duckdb.DuckDBPyConnection, relation: str, rules: MappingProxyType[str, Rule]
) -> str:
    """SQL keeping rows where every rule is TRUE (NULL counts as a violation)."""
    predicate = " AND ".join(f"coalesce(({r.expression}), false)" for r in rules.values())
    return f"SELECT * FROM {relation} WHERE {predicate}"


def silver_relation(con: duckdb.DuckDBPyConnection, bronze: str) -> str:
    con.execute(f"CREATE OR REPLACE VIEW silver_pre AS {silver_select_sql(bronze, 'duckdb')}")
    return "silver_pre"


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect()
    yield connection
    connection.close()
