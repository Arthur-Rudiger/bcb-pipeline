-- =========================================================
-- 003: analytical views (derived from observations)
-- Views store no data: they are saved queries, always up to date.
-- =========================================================

-- Safe to re-run: views hold no data, so drop and recreate them all
-- inside one transaction (all or nothing).
BEGIN;

DROP VIEW IF EXISTS
    pipeline_health, monthly_indicators, real_interest,
    dollar_monthly, selic_monthly, ipca_12m;

-- IPCA accumulated over 12 months, compounded:
--   (1 + m1/100) * (1 + m2/100) * ... * (1 + m12/100) - 1
-- computed as exp(sum(ln(...))) because SQL has no product() aggregate.
-- RANGE over 11 months (not ROWS over 11 rows): a missing month can never
-- make the window silently stretch over 13 months.
CREATE VIEW ipca_12m AS
SELECT
    ref_date AS ref_month,
    value    AS ipca_month,
    round(((exp(sum(ln(1 + value / 100)) OVER w) - 1) * 100)::numeric, 2) AS ipca_12m,
    count(*) OVER w AS months_in_window
FROM observations
WHERE series_id = 433
WINDOW w AS (ORDER BY ref_date RANGE BETWEEN INTERVAL '11 months' PRECEDING AND CURRENT ROW);

-- Selic target on the last available day of each month
CREATE VIEW selic_monthly AS
SELECT DISTINCT ON (date_trunc('month', ref_date))
    date_trunc('month', ref_date)::date AS ref_month,
    value AS selic
FROM observations
WHERE series_id = 432
ORDER BY date_trunc('month', ref_date), ref_date DESC;

-- Dollar: monthly average and month-end close
CREATE VIEW dollar_monthly AS
SELECT
    date_trunc('month', ref_date)::date AS ref_month,
    round(avg(value), 4) AS dollar_avg,
    (array_agg(value ORDER BY ref_date DESC))[1] AS dollar_close,
    count(*) AS business_days
FROM observations
WHERE series_id = 1
GROUP BY 1;

-- Ex-post real interest rate (Fisher equation):
--   (1 + nominal) / (1 + inflation) - 1
-- using month-end Selic and trailing 12-month IPCA.
CREATE VIEW real_interest AS
SELECT
    s.ref_month,
    s.selic,
    i.ipca_12m,
    round(((1 + s.selic / 100) / (1 + i.ipca_12m / 100) - 1) * 100, 2) AS real_rate
FROM selic_monthly s
JOIN ipca_12m i USING (ref_month)
WHERE i.months_in_window = 12;

-- One wide monthly table for dashboards and analysis
CREATE VIEW monthly_indicators AS
SELECT
    s.ref_month,
    s.selic,
    i.ipca_month,
    i.ipca_12m,
    r.real_rate,
    d.dollar_avg,
    d.dollar_close
FROM selic_monthly s
LEFT JOIN ipca_12m       i USING (ref_month)
LEFT JOIN real_interest  r USING (ref_month)
LEFT JOIN dollar_monthly d USING (ref_month);

-- Pipeline health: latest data, latest run, and whether data is stale.
-- Monthly series are published with a delay (IPCA for October comes out in
-- November, dated 01/10), so "stale" depends on the frequency.
CREATE VIEW pipeline_health AS
SELECT
    s.series_id,
    s.name,
    s.frequency,
    o.last_date,
    current_date - o.last_date AS days_since_last_value,
    coalesce(
        current_date - o.last_date > CASE s.frequency WHEN 'daily' THEN 5 ELSE 75 END,
        true
    ) AS is_stale,
    r.status      AS last_run_status,
    r.finished_at AS last_run_at
FROM series s
LEFT JOIN (
    SELECT series_id, max(ref_date) AS last_date
    FROM observations
    GROUP BY series_id
) o USING (series_id)
LEFT JOIN LATERAL (
    SELECT status, finished_at
    FROM ingestion_runs
    WHERE series_id = s.series_id
    ORDER BY run_id DESC
    LIMIT 1
) r ON true;

COMMIT;
