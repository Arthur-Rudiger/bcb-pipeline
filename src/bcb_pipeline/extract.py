"""Extract time series from Banco Central do Brasil SGS API"""
import logging
from datetime import date, timedelta

import pandas as pd  # For data manipulation
import requests  # For requesting data from the API
from requests.adapters import HTTPAdapter  # For HTTP interaction
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

BASE_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_id}/dados"    #API from BCB
WINDOW = timedelta(days = 5 * 365)                                                # API limit is 10 years for daily series; 5 is faster and safer
TIMEOUT = 60                                                                    # seconds per request


class SeriesNotFoundError(Exception):
    """Series code does not exist in SGS."""


# HTTP session with automatic retries
def make_session() -> requests.Session:
    
    retry = Retry(
        total = 5,
        backoff_factor = 1,         # wait 1s, 2s, 4s, 8s... between tries
        status_forcelist= [429, 500, 502, 503, 504],  # retry only temporary errors
        allowed_methods= ["GET"],
    )

    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "bcb-pipeline/0.1"
    
    return session

# Split a long date range into smaller windows
def date_windows(start: date, end: date, size: timedelta = WINDOW):
    
    current = start
    
    while current <= end:
        stop = min(current + size, end)
        yield current, stop
        current = stop + timedelta(days = 1)

# Fetch one window
def fetch_window(session, series_id: int, start: date, end: date) -> list[dict]:
    
    response = session.get(
        BASE_URL.format(series_id=series_id),
        params={
            "formato": "json",
            "dataInicial": start.strftime("%d/%m/%Y"),
            "dataFinal": end.strftime("%d/%m/%Y"),
        },
        timeout=TIMEOUT,
    )

    if response.status_code == 404:
        
        if "nao existe" in response.text:            # "A serie nao existe!"
            raise SeriesNotFoundError(f"SGS series {series_id} does not exist")
        
        return []                                    # "Value(s) not found" = no data in window
    
    response.raise_for_status()                      # any other error: fail loudly
    
    return response.json()

# Convert raw records into a typed DataFrame
def parse(records: list[dict], series_id: int) -> pd.DataFrame:
    
    if not records:
        return pd.DataFrame({
            "series_id": pd.Series(dtype="int64"),
            "ref_date": pd.Series(dtype="datetime64[ns]"),
            "value": pd.Series(dtype="float64"),
        })
    
    raw = pd.DataFrame(records)
    
    df = pd.DataFrame({
        "series_id": series_id,
        "ref_date": pd.to_datetime(raw["data"], format="%d/%m/%Y").astype("datetime64[ns]"),
        "value": pd.to_numeric(raw["valor"], errors="coerce"),  # bad values become NaN
    })
    # Monthly series: SGS returns a month in every window that touches it,
    # so the month at each window boundary appears twice.
    return df.drop_duplicates(subset="ref_date", keep="last").reset_index(drop=True)

# Public function: the only one the rest of the pipeline calls
def fetch_series(series_id: int, start: date, end: date, session=None) -> pd.DataFrame:
    
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    
    session = session or make_session()
    records: list[dict] = []
    
    for window_start, window_end in date_windows(start, end):
        batch = fetch_window(session, series_id, window_start, window_end)
        log.info("series %s %s -> %s: %d rows", series_id, window_start, window_end, len(batch))
        records.extend(batch)
    
    return parse(records, series_id)
