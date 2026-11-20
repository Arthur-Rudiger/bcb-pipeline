from datetime import date

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from bcb_pipeline.load import last_loaded_date, upsert_observations

TEST_SERIES = 9999  # created inside each test's transaction, never committed


@pytest.fixture(autouse=True)
def test_series(conn):
    conn.execute(text("INSERT INTO series VALUES (:s, 'Test series', 'x', 'daily')"),
                 {"s": TEST_SERIES})


def make_df(values, series_id=TEST_SERIES):
    dates = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return pd.DataFrame({
        "series_id": series_id,
        "ref_date": pd.Series(dates).astype("datetime64[ns]"),
        "value": pd.Series(values, dtype="float64"),
    })


def stored_values(conn):
    rows = conn.execute(
        text("SELECT value FROM observations WHERE series_id = :s ORDER BY ref_date"),
        {"s": TEST_SERIES},
    )
    return [float(r.value) for r in rows]


def test_first_load_inserts_everything(conn):
    result = upsert_observations(conn, make_df([10.0, 11.0, 12.0]))
    assert result == {"inserted": 3, "updated": 0, "unchanged": 0}
    assert stored_values(conn) == [10.0, 11.0, 12.0]


def test_second_identical_load_changes_nothing(conn):
    df = make_df([10.0, 11.0, 12.0])
    upsert_observations(conn, df)
    result = upsert_observations(conn, df)
    assert result == {"inserted": 0, "updated": 0, "unchanged": 3}
    assert len(stored_values(conn)) == 3  # idempotent: no duplicates


def test_revised_value_is_updated(conn):
    upsert_observations(conn, make_df([10.0, 11.0, 12.0]))
    result = upsert_observations(conn, make_df([10.0, 11.5, 12.0]))
    assert result == {"inserted": 0, "updated": 1, "unchanged": 2}
    assert stored_values(conn) == [10.0, 11.5, 12.0]


def test_mixed_batch_counts_each_outcome(conn):
    upsert_observations(conn, make_df([10.0, 11.0]))
    result = upsert_observations(conn, make_df([10.0, 99.0, 12.0]))
    assert result == {"inserted": 1, "updated": 1, "unchanged": 1}


def test_empty_frame_is_a_no_op(conn):
    assert upsert_observations(conn, make_df([])) == {"inserted": 0, "updated": 0, "unchanged": 0}


def test_unknown_series_is_rejected_by_foreign_key(conn):
    with pytest.raises(IntegrityError):
        upsert_observations(conn, make_df([1.0], series_id=123456))


def test_last_loaded_date(conn):
    assert last_loaded_date(conn, TEST_SERIES) is None
    upsert_observations(conn, make_df([1.0, 2.0, 3.0]))
    assert last_loaded_date(conn, TEST_SERIES) == date(2026, 1, 3)
