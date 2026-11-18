-- =========================================================
-- bcb-pipeline schema
-- Runs automatically the first time the Postgres container
-- creates its data volume (docker-entrypoint-initdb.d).
-- =========================================================

-- Catalogue of tracked series (codes from the BCB SGS system)
CREATE TABLE series (
    series_id  INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    unit       TEXT    NOT NULL,
    frequency  TEXT    NOT NULL CHECK (frequency IN ('daily', 'monthly'))
);

-- One row per series per date (long / tidy format)
CREATE TABLE observations (
    series_id  INTEGER       NOT NULL REFERENCES series (series_id),
    ref_date   DATE          NOT NULL,
    value      NUMERIC(18,6) NOT NULL,
    loaded_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, ref_date)
);

-- Audit log: one row per series per pipeline run
CREATE TABLE ingestion_runs (
    run_id        BIGSERIAL   PRIMARY KEY,
    series_id     INTEGER     NOT NULL REFERENCES series (series_id),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    rows_fetched  INTEGER     CHECK (rows_fetched >= 0),
    status        TEXT        NOT NULL DEFAULT 'running'
                              CHECK (status IN ('running', 'success', 'failed')),
    error         TEXT
);

-- Initial series
INSERT INTO series (series_id, name, unit, frequency) VALUES
    (432, 'Selic meta',              '% a.a.', 'daily'),
    (433, 'IPCA',                    '% a.m.', 'monthly'),
    (1,   'Dólar comercial (venda)', 'R$/US$', 'daily');
