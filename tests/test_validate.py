import numpy as np
import pandas as pd
import pytest

from bcb_pipeline.validate import DataValidationError, validate


def make_df(values, dates=None, series_id=432):
    """Build a frame shaped exactly like extract.fetch_series output."""
    dates = dates or [f"2026-10-{d:02d}" for d in range(1, len(values) + 1)]
    return pd.DataFrame({
        "series_id": series_id,
        "ref_date": pd.to_datetime(dates).astype("datetime64[ns]"),
        "value": pd.Series(values, dtype="float64"),
    })


def test_valid_data_passes_unchanged():
    df = make_df([13.75, 13.75, 14.0])
    pd.testing.assert_frame_equal(validate(df), df)


def test_empty_frame_passes():
    assert validate(make_df([])).empty


def test_duplicate_dates_fail():
    with pytest.raises(DataValidationError, match="uniqueness"):
        validate(make_df([13.75, 13.75], dates=["2026-10-01", "2026-10-01"]))


def test_missing_value_fails():
    with pytest.raises(DataValidationError, match="not_nullable"):
        validate(make_df([13.75, np.nan]))


@pytest.mark.parametrize(
    ("series_id", "bad_value"),
    [(432, 137.5), (433, 25.0), (1, 0.05)],  # decimal-point slips
)
def test_out_of_range_fails(series_id, bad_value):
    with pytest.raises(DataValidationError, match="in_range"):
        validate(make_df([bad_value], series_id=series_id))


def test_future_date_fails():
    with pytest.raises(DataValidationError, match="future"):
        validate(make_df([13.75], dates=["2031-01-01"]))


def test_unknown_series_fails():
    with pytest.raises(DataValidationError, match="isin"):
        validate(make_df([1.0], series_id=999))


def test_extra_column_fails():
    with pytest.raises(DataValidationError, match="column_in_schema"):
        validate(make_df([13.75]).assign(source="bcb"))


def test_reports_all_problems_at_once():
    with pytest.raises(DataValidationError, match="2 problem"):
        validate(make_df([np.nan, 137.5]))
