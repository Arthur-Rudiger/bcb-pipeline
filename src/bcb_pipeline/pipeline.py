"""Run the pipeline: extract -> validate -> load, recording every run."""
import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Engine, text

from .config import BACKFILL_START, LOOKBACK_DAYS
from .extract import fetch_series
from .load import get_engine, last_loaded_date, upsert_observations
from .validate import validate

log = logging.getLogger("bcb_pipeline")

START_RUN_SQL = text(
    "INSERT INTO ingestion_runs (series_id) VALUES (:series_id) RETURNING run_id"
)
FINISH_RUN_SQL = text("""
    UPDATE ingestion_runs
       SET finished_at   = now(),
           status        = :status,
           rows_fetched  = :fetched,
           rows_inserted = :inserted,
           rows_updated  = :updated,
           error         = :error
     WHERE run_id = :run_id
""")
KNOWN_SERIES_SQL = text("SELECT series_id FROM series ORDER BY series_id")


@dataclass
class RunResult:
    series_id: int
    status: str = "running"
    start: date | None = None
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    error: str | None = None


def start_date_for(last_loaded: date | None, full: bool = False) -> date:
    """First run or --full: everything. Otherwise: re-fetch the last LOOKBACK_DAYS."""
    if full or last_loaded is None:
        return BACKFILL_START
    return max(BACKFILL_START, last_loaded - timedelta(days=LOOKBACK_DAYS))


def _finish(engine: Engine, run_id: int, result: RunResult, conn=None) -> None:
    params = {
        "run_id": run_id,
        "status": result.status,
        "fetched": result.fetched,
        "inserted": result.inserted,
        "updated": result.updated,
        "error": result.error,
    }
    if conn is not None:
        conn.execute(FINISH_RUN_SQL, params)
    else:
        with engine.begin() as new_conn:
            new_conn.execute(FINISH_RUN_SQL, params)


def run_series(
    engine: Engine,
    series_id: int,
    *,
    full: bool = False,
    today: date | None = None,
    fetch=fetch_series,
    check=validate,
) -> RunResult:
    """Extract, validate and load one series. Never raises: failures are recorded."""
    today = today or date.today()

    # Transaction 1: register the run. Committed immediately, so the run is
    # visible in ingestion_runs even if everything after this crashes.
    with engine.begin() as conn:
        run_id = conn.execute(START_RUN_SQL, {"series_id": series_id}).scalar_one()
        start = start_date_for(last_loaded_date(conn, series_id), full)

    result = RunResult(series_id=series_id, start=start)
    try:
        df = check(fetch(series_id, start, today))
        result.fetched = len(df)
        # Transaction 2: the data and the "success" record commit together,
        # or not at all.
        with engine.begin() as conn:
            counts = upsert_observations(conn, df)
            result.inserted, result.updated = counts["inserted"], counts["updated"]
            result.status = "success"
            _finish(engine, run_id, result, conn)
    except Exception as exc:  # record ANY failure, then carry on with other series
        result.status = "failed"
        result.inserted = result.updated = 0
        result.error = f"{type(exc).__name__}: {exc}"[:2000]
        _finish(engine, run_id, result)  # transaction 3: record the failure
        log.exception("series %s failed", series_id)
    else:
        log.info(
            "series %s ok: fetched %d from %s, inserted %d, updated %d",
            series_id, result.fetched, start, result.inserted, result.updated,
        )
    return result


def run(
    series_ids: list[int] | None = None,
    *,
    full: bool = False,
    engine: Engine | None = None,
    **kwargs,
) -> list[RunResult]:
    """Run every requested series (default: all in the series table)."""
    engine = engine or get_engine()
    with engine.connect() as conn:
        known = list(conn.execute(KNOWN_SERIES_SQL).scalars())
    series_ids = series_ids or known
    unknown = sorted(set(series_ids) - set(known))
    if unknown:
        raise ValueError(f"unknown series {unknown}; known: {known}")
    return [run_series(engine, sid, full=full, **kwargs) for sid in series_ids]


def print_summary(results: list[RunResult]) -> None:
    print(f"\n{'series':>8} {'status':<8} {'from':<10} {'fetched':>8} {'new':>6} {'upd':>5}")
    for r in results:
        print(
            f"{r.series_id:>8} {r.status:<8} {r.start!s:<10} "
            f"{r.fetched:>8} {r.inserted:>6} {r.updated:>5}"
        )
        if r.error:
            print(f"{'':>8} error: {r.error.splitlines()[0][:100]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bcb-pipeline",
        description="Load Banco Central (SGS) series into PostgreSQL.",
    )
    parser.add_argument("--series", type=int, nargs="+", metavar="ID",
                        help="series codes to load (default: all in the series table)")
    parser.add_argument("--full", action="store_true",
                        help=f"re-fetch everything since {BACKFILL_START}")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        results = run(args.series, full=args.full)
    except ValueError as exc:
        parser.error(str(exc))  # prints usage and exits with code 2
    print_summary(results)
    return 1 if any(r.status == "failed" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
