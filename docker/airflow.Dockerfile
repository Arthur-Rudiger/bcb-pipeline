# Airflow with the bcb-pipeline package installed.
FROM apache/airflow:3.3.2-python3.14

COPY --chown=airflow:root pyproject.toml README.md LICENSE /opt/bcb/
COPY --chown=airflow:root src /opt/bcb/src
RUN pip install --no-cache-dir /opt/bcb
