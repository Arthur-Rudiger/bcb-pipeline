"""
### bcb_daily

Loads the Banco Central SGS series into Postgres every business day, then
checks that no series is stale (using the `pipeline_health` view).

- One task per series: a failure in one series never blocks the others, and
  each one retries on its own.
- The work itself is the `bcb-pipeline` CLI, so Airflow only schedules,
  retries and records. The same command works by hand or in cron.
"""
from datetime import timedelta

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag, task

SERIES = {432: "selic", 433: "ipca", 1: "dollar"}


@dag(
    dag_id="bcb_daily",
    schedule="0 20 * * 1-5",  # 20:00 Brasília time, Monday to Friday
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Sao_Paulo"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
        "execution_timeout": timedelta(minutes=20),
    },
    tags=["bcb", "etl"],
    doc_md=__doc__,
)
def bcb_daily():
    loads = [
        BashOperator(task_id=f"load_{name}", bash_command=f"bcb-pipeline --series {series_id}")
        for series_id, name in SERIES.items()
    ]

    @task
    def check_freshness():
        from sqlalchemy import text

        from bcb_pipeline.load import get_engine

        with get_engine().connect() as conn:
            stale = conn.execute(text(
                "SELECT series_id, name, days_since_last_value FROM pipeline_health WHERE is_stale"
            )).all()
        if stale:
            raise ValueError(f"Stale series: {stale}")
        print("All series are fresh.")

    loads >> check_freshness()


bcb_daily()
