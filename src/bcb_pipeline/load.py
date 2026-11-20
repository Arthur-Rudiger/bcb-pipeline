"""Load validated data into PostgreSQL."""
from datetime import date

import pandas as pd
from sqlalchemy import Connection, Engine, create_engine, text

from .config import DB_URL

# One statement for the whole batch:
#   unnest() turns three parallel arrays into rows,
#   ON CONFLICT turns "insert" into "insert or update",
#   the WHERE skips rows whose value did not change,
#   RETURNING tells us which rows were new (xmax = 0) and which were updated.
UPSERT_SQL = text("""
    INSERT INTO observations (series_id, ref_date, value)
    SELECT *
    FROM unnest(
        CAST(:series_ids AS integer[]),
        CAST(:ref_dates  AS date[]),
        CAST(:values     AS numeric[])
    )
    ON CONFLICT (series_id, ref_date) DO UPDATE
        SET value     = EXCLUDED.value,
            loaded_at = now()
        WHERE observations.value IS DISTINCT FROM EXCLUDED.value
    RETURNING (xmax = 0) AS inserted
""")

LAST_DATE_SQL = text("SELECT max(ref_date) FROM observations WHERE series_id = :series_id")


def get_engine(url: str = DB_URL) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def upsert_observations(conn: Connection, df: pd.DataFrame) -> dict[str, int]:
    """Insert new rows, update changed ones, skip identical ones.

    Runs inside the caller's transaction. Returns counts per outcome.
    """
    if df.empty:
        return {"inserted": 0, "updated": 0, "unchanged": 0}
    result = conn.execute(UPSERT_SQL, {
        "series_ids": df["series_id"].astype(int).tolist(),
        "ref_dates": df["ref_date"].dt.date.tolist(),
        "values": df["value"].astype(float).tolist(),
    })
    flags = [row.inserted for row in result]
    inserted = sum(flags)
    updated = len(flags) - inserted
    return {"inserted": inserted, "updated": updated, "unchanged": len(df) - len(flags)}


def last_loaded_date(conn: Connection, series_id: int) -> date | None:
    """Most recent ref_date stored for a series, or None if it has no data yet."""
    return conn.execute(LAST_DATE_SQL, {"series_id": series_id}).scalar()
