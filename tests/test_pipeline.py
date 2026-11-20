from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import text

from bcb_pipeline.config import BACKFILL_START, LOOKBACK_DAYS
from bcb_pipeline.pipeline import run, run_series, start_date_for
from bcb_pipeline.validate import DataValidationError

TODAY = date(2026, 1, 10)
SERIES_A, SERIES_B = 9998, 9997  # test-only series, removed after each test


# --- pure logic: no database needed ------------------------------------------
def test_first_run_starts_at_backfill():
    assert start_date_for(None) == BACKFILL_START


def test_incremental_run_starts_lookback_days_before_last_date():
    assert start_date_for(date(2026, 1, 10)) == date(2026, 1, 10) - timedelta(days=LOOKBACK_DAYS)


def test_full_run_ignores_last_date():
    assert start_date_for(date(2026, 1, 10), full=True) == BACKFILL_START


# --- helpers for the database tests ------------------------------------------
@pytest.fixture
def series(engine):
    """Commit two test series (the pipeline opens its own transactions), then clean up."""
    ids = (SERIES_A, SERIES_B)
    with engine.begin() as conn:
        for sid in ids:
            conn.execute(text("INSERT INTO series VALUES (:s, 'Pipeline test', 'x', 'daily')"),
                         {"s": sid})
    yield ids
    with engine.begin() as conn:
        params = [{"s": sid} for sid in ids]
        conn.execute(text("DELETE FROM observations WHERE series_id = :s"), params)
        conn.execute(text("DELETE FROM ingestion_runs WHERE series_id = :s"), params)
        conn.execute(text("DELETE FROM series WHERE series_id = :s"), params)


class FakeFetch:
    """Stands in for extract.fetch_series: returns the last 3 days up to `end`."""

    def __init__(self, fail_for=()):
        self.calls, self.fail_for = [], set(fail_for)

    def __call__(self, series_id, start, end):
        self.calls.append((series_id, start, end))
        if series_id in self.fail_for:
            raise RuntimeError("API down")
        dates = pd.date_range(end - timedelta(days=2), end, freq="D")
        return pd.DataFrame({
            "series_id": series_id,
            "ref_date": pd.Series(dates).astype("datetime64[ns]"),
            "value": [1.0, 2.0, 3.0],
        })


def no_check(df):
    return df  # test series aren't in VALID_RANGES, so skip real validation


def last_run(engine, sid):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT * FROM ingestion_runs WHERE series_id = :s ORDER BY run_id DESC LIMIT 1"),
            {"s": sid},
        ).one()


def count_obs(engine, sid):
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM observations WHERE series_id = :s"),
                            {"s": sid}).scalar()


# --- database tests ----------------------------------------------------------
def test_first_run_backfills_and_logs_success(engine, series):
    fetch = FakeFetch()
    result = run_series(engine, SERIES_A, today=TODAY, fetch=fetch, check=no_check)

    assert fetch.calls[0][1] == BACKFILL_START
    assert (result.status, result.inserted, result.updated) == ("success", 3, 0)
    log_row = last_run(engine, SERIES_A)
    assert log_row.status == "success"
    assert (log_row.rows_fetched, log_row.rows_inserted, log_row.rows_updated) == (3, 3, 0)
    assert log_row.finished_at is not None


def test_second_run_is_incremental_and_idempotent(engine, series):
    fetch = FakeFetch()
    run_series(engine, SERIES_A, today=TODAY, fetch=fetch, check=no_check)
    result = run_series(engine, SERIES_A, today=TODAY, fetch=fetch, check=no_check)

    assert fetch.calls[1][1] == TODAY - timedelta(days=LOOKBACK_DAYS)
    assert (result.inserted, result.updated) == (0, 0)
    assert count_obs(engine, SERIES_A) == 3


def test_fetch_failure_is_logged_and_loads_nothing(engine, series):
    result = run_series(engine, SERIES_A, today=TODAY,
                        fetch=FakeFetch(fail_for={SERIES_A}), check=no_check)

    assert result.status == "failed" and "API down" in result.error
    log_row = last_run(engine, SERIES_A)
    assert log_row.status == "failed" and "API down" in log_row.error
    assert count_obs(engine, SERIES_A) == 0


def test_validation_failure_loads_nothing(engine, series):
    def reject(df):
        raise DataValidationError("bad data")

    result = run_series(engine, SERIES_A, today=TODAY, fetch=FakeFetch(), check=reject)

    assert result.status == "failed" and "DataValidationError" in result.error
    assert count_obs(engine, SERIES_A) == 0


def test_one_failing_series_does_not_stop_the_others(engine, series):
    results = run([SERIES_A, SERIES_B], engine=engine, today=TODAY,
                  fetch=FakeFetch(fail_for={SERIES_A}), check=no_check)

    assert [r.status for r in results] == ["failed", "success"]
    assert count_obs(engine, SERIES_B) == 3


def test_unknown_series_is_rejected_before_running(engine):
    with pytest.raises(ValueError, match="unknown series"):
        run([123456], engine=engine)
