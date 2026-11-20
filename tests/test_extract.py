import json
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from bcb_pipeline.extract import SeriesNotFoundError, date_windows, fetch_series

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sgs_1_sample.json").read_text())


# --- fake HTTP objects: no network needed ---------------------------------
class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code, self._payload, self.text = status_code, payload, text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response):
        self.response, self.calls = response, 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.response


# --- date_windows -----------------------------------------------------------
def test_windows_cover_range_without_gaps_or_overlaps():
    windows = list(date_windows(date(2000, 1, 1), date(2026, 11, 18)))
    assert windows[0][0] == date(2000, 1, 1)
    assert windows[-1][1] == date(2026, 11, 18)
    for (_, end), (next_start, _) in pairwise(windows):
        assert next_start == end + timedelta(days=1)


def test_single_day_is_one_window():
    assert list(date_windows(date(2026, 1, 1), date(2026, 1, 1))) == [(date(2026, 1, 1), date(2026, 1, 1))]


# --- fetch_series -----------------------------------------------------------
def test_parses_real_sample():
    session = FakeSession(FakeResponse(payload=FIXTURE))
    df = fetch_series(1, date(2026, 11, 10), date(2026, 11, 17), session=session)
    assert list(df.columns) == ["series_id", "ref_date", "value"]
    assert len(df) == len(FIXTURE)
    assert df["value"].iloc[0] == float(FIXTURE[0]["valor"])
    assert str(df["ref_date"].dtype) == "datetime64[ns]"


def test_no_data_404_returns_empty_frame():
    session = FakeSession(FakeResponse(404, text='{"erro": "Value(s) not found"}'))
    df = fetch_series(432, date(2030, 1, 1), date(2030, 1, 31), session=session)
    assert df.empty and list(df.columns) == ["series_id", "ref_date", "value"]


def test_unknown_series_raises():
    session = FakeSession(FakeResponse(404, text="A serie nao existe!"))
    with pytest.raises(SeriesNotFoundError):
        fetch_series(99999999, date(2026, 1, 1), date(2026, 1, 31), session=session)


def test_long_range_makes_several_requests():
    session = FakeSession(FakeResponse(payload=[]))
    fetch_series(432, date(2000, 1, 1), date(2026, 1, 1), session=session)
    assert session.calls == 6


def test_start_after_end_is_rejected():
    with pytest.raises(ValueError):
        fetch_series(432, date(2026, 2, 1), date(2026, 1, 1), session=FakeSession(FakeResponse()))

def test_monthly_overlap_at_window_edges_is_removed():
    # the API returns the boundary month in both windows
    session = FakeSession(FakeResponse(payload=[{"data": "01/12/2004", "valor": "0.86"}]))
    df = fetch_series(433, date(2000, 1, 1), date(2026, 1, 1), session=session)
    assert len(df) == 1