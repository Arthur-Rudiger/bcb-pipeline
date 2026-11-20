"""Streamlit dashboard for the BCB pipeline.

Reads only from the SQL views (sql/003_views.sql): every number here is
computed in the database, so the dashboard and any other tool always agree.

Run:  streamlit run dashboard/app.py
"""
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

from bcb_pipeline.load import get_engine

st.set_page_config(page_title="Brazil macro indicators", page_icon="📈", layout="wide")


@st.cache_resource
def engine():
    return get_engine()


@st.cache_data(ttl=600)  # re-query at most every 10 minutes
def load(sql: str) -> pd.DataFrame:
    with engine().connect() as conn:
        return pd.read_sql(text(sql), conn)


try:
    monthly = load("SELECT * FROM monthly_indicators ORDER BY ref_month")
    health = load("SELECT * FROM pipeline_health ORDER BY series_id")
except Exception as exc:  # noqa: BLE001 - show any DB problem to the user
    st.error(f"Could not read the database: {exc}")
    st.info("Start it with `docker compose up -d` and load data with `bcb-pipeline`.")
    st.stop()

if monthly.empty:
    st.warning("No data yet. Run `bcb-pipeline` to load the series.")
    st.stop()

monthly["ref_month"] = pd.to_datetime(monthly["ref_month"])
for col in monthly.columns.drop("ref_month"):
    monthly[col] = pd.to_numeric(monthly[col])

# ---------------------------------------------------------------- sidebar
first, last = monthly["ref_month"].min().date(), monthly["ref_month"].max().date()
with st.sidebar:
    st.header("Filters")
    start, end = st.slider(
        "Period", min_value=first, max_value=last,
        value=(max(first, date(last.year - 10, 1, 1)), last), format="MM/YYYY",
    )
    st.caption("Source: Banco Central do Brasil, SGS series 432 (Selic), 433 (IPCA), 1 (USD/BRL).")

view = monthly[(monthly["ref_month"].dt.date >= start) & (monthly["ref_month"].dt.date <= end)]

# ---------------------------------------------------------------- header + KPIs
st.title("Brazil macro indicators")
st.caption("Selic, inflation, real interest rate and the dollar, loaded daily from the BCB API.")


def latest(col):
    """Latest non-empty value of a column, its month, and the change over 12 months."""
    s = monthly.set_index("ref_month")[col].dropna()
    now = s.iloc[-1]
    year_ago = s.get(s.index[-1] - pd.DateOffset(months=12))
    return s.index[-1], now, (None if year_ago is None else now - year_ago)


kpis = [
    ("Selic target", "selic", "{:.2f}%"),
    ("IPCA, 12 months", "ipca_12m", "{:.2f}%"),
    ("Real interest rate", "real_rate", "{:.2f}%"),
    ("USD/BRL, month-end", "dollar_close", "R$ {:.4f}"),
]
for column, (label, col, fmt) in zip(st.columns(len(kpis)), kpis, strict=True):
    month, value, delta = latest(col)
    column.metric(
        f"{label} ({month:%m/%Y})",
        fmt.format(value),
        None if delta is None else f"{delta:+.2f} vs 12 months before",
        delta_color="off",
    )

# ---------------------------------------------------------------- charts
left, right = st.columns(2)

with left:
    st.subheader("Selic vs 12-month inflation")
    long = view.melt("ref_month", ["selic", "ipca_12m"], var_name="series", value_name="%")
    long["series"] = long["series"].map({"selic": "Selic", "ipca_12m": "IPCA 12m"})
    fig = px.line(long, x="ref_month", y="%", color="series")
    fig.update_layout(xaxis_title=None, legend_title=None, hovermode="x unified")
    st.plotly_chart(fig)

with right:
    st.subheader("Real interest rate (ex-post)")
    fig = px.area(view.dropna(subset=["real_rate"]), x="ref_month", y="real_rate")
    fig.add_hline(y=0, line_dash="dot")
    fig.update_layout(xaxis_title=None, yaxis_title="%", hovermode="x unified")
    st.plotly_chart(fig)
    st.caption("Fisher equation: (1 + Selic) / (1 + IPCA 12m) − 1, using past inflation.")

st.subheader("Dollar (USD/BRL)")
names = {"dollar_avg": "Monthly average", "dollar_close": "Month-end close"}
fig = px.line(view, x="ref_month", y=list(names))
fig.for_each_trace(lambda t: t.update(name=names[t.name]))
fig.update_layout(xaxis_title=None, yaxis_title="R$", legend_title=None, hovermode="x unified")
st.plotly_chart(fig)

# ---------------------------------------------------------------- data + health
with st.expander("Data table and CSV download"):
    st.dataframe(view.sort_values("ref_month", ascending=False), hide_index=True)
    st.download_button(
        "Download CSV", view.to_csv(index=False).encode(), "bcb_monthly_indicators.csv", "text/csv"
    )

st.subheader("Pipeline health")
st.dataframe(
    health[["series_id", "name", "frequency", "last_date", "days_since_last_value",
            "is_stale", "last_run_status", "last_run_at"]],
    hide_index=True,
)
if health["is_stale"].any():
    st.warning("Some series are stale: check the latest pipeline run.")
