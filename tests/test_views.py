"""Tests for the SQL views, using hand-made data from 1990.

Each test runs in a rolled-back transaction (the `conn` fixture), and only
looks at rows before 2000, so real loaded data never interferes.
"""
import pytest
from sqlalchemy import text

IPCA, SELIC, DOLLAR = 433, 432, 1


def insert(conn, series_id, rows):
    conn.execute(
        text("INSERT INTO observations (series_id, ref_date, value) VALUES (:s, :d, :v)"),
        [{"s": series_id, "d": d, "v": v} for d, v in rows],
    )


def query(conn, sql):
    return conn.execute(text(sql)).mappings().all()


def months_1990(value, skip=()):
    return [(f"1990-{m:02d}-01", value) for m in range(1, 13) if m not in skip]


def test_ipca_12m_compounds_monthly_rates(conn):
    insert(conn, IPCA, months_1990(1.0))  # 1% every month
    rows = query(conn, "SELECT * FROM ipca_12m WHERE ref_month < '2000-01-01' ORDER BY ref_month")
    december = rows[-1]
    assert december["months_in_window"] == 12
    assert float(december["ipca_12m"]) == 12.68  # 1.01**12 - 1 = 12.68%, not 12%
    assert rows[0]["months_in_window"] == 1


def test_missing_month_shrinks_the_window_instead_of_stretching_it(conn):
    insert(conn, IPCA, months_1990(1.0, skip={6}))
    december = query(conn, "SELECT * FROM ipca_12m WHERE ref_month = '1990-12-01'")[0]
    assert december["months_in_window"] == 11


def test_selic_monthly_takes_the_last_day_of_the_month(conn):
    insert(conn, SELIC, [("1990-01-01", 10.0), ("1990-01-15", 11.0), ("1990-01-31", 12.0)])
    row = query(conn, "SELECT * FROM selic_monthly WHERE ref_month = '1990-01-01'")[0]
    assert float(row["selic"]) == 12.0


def test_real_interest_uses_fisher_equation(conn):
    insert(conn, IPCA, months_1990(1.0))
    insert(conn, SELIC, [("1990-12-31", 10.0)])
    row = query(conn, "SELECT * FROM real_interest WHERE ref_month = '1990-12-01'")[0]
    # (1.10 / 1.1268) - 1 = -2.38%  (simple subtraction would wrongly give -2.68)
    assert float(row["real_rate"]) == -2.38


def test_real_interest_requires_a_complete_12_month_window(conn):
    insert(conn, IPCA, months_1990(1.0, skip={6}))
    insert(conn, SELIC, [("1990-12-31", 10.0)])
    assert query(conn, "SELECT * FROM real_interest WHERE ref_month = '1990-12-01'") == []


def test_dollar_monthly_average_and_close(conn):
    insert(conn, DOLLAR, [("1990-01-02", 1.0), ("1990-01-03", 2.0), ("1990-01-04", 3.0)])
    row = query(conn, "SELECT * FROM dollar_monthly WHERE ref_month = '1990-01-01'")[0]
    assert float(row["dollar_avg"]) == 2.0
    assert float(row["dollar_close"]) == 3.0
    assert row["business_days"] == 3


@pytest.mark.parametrize(("frequency", "expected_stale"), [("daily", True), ("monthly", True)])
def test_pipeline_health_flags_old_data_as_stale(conn, frequency, expected_stale):
    conn.execute(text("INSERT INTO series VALUES (9999, 'Test', 'x', :f)"), {"f": frequency})
    insert(conn, 9999, [("1990-01-01", 1.0)])
    row = query(conn, "SELECT * FROM pipeline_health WHERE series_id = 9999")[0]
    assert row["is_stale"] is expected_stale


def test_pipeline_health_series_without_data_is_stale(conn):
    conn.execute(text("INSERT INTO series VALUES (9999, 'Test', 'x', 'daily')"))
    row = query(conn, "SELECT * FROM pipeline_health WHERE series_id = 9999")[0]
    assert row["last_date"] is None and row["is_stale"] is True


def test_pipeline_health_recent_monthly_value_is_fresh(conn):
    conn.execute(text("INSERT INTO series VALUES (9999, 'Test', 'x', 'monthly')"))
    conn.execute(text(
        "INSERT INTO observations (series_id, ref_date, value) "
        "VALUES (9999, date_trunc('month', current_date - 40)::date, 1.0)"
    ))
    row = query(conn, "SELECT * FROM pipeline_health WHERE series_id = 9999")[0]
    assert row["is_stale"] is False
