"""Validate extracted data before it is loaded into the database."""
import pandas as pd
import pandera.pandas as pa

# Plausible value ranges per series. Values outside are almost certainly bugs
# (parsing errors, wrong series code), not real economic events.
VALID_RANGES: dict[int, tuple[float, float]] = {
    432: (0.0, 50.0),   # Selic meta, % a.a.   (real 2000-2026: 2.0 to 26.5)
    433: (-5.0, 10.0),  # IPCA, % a.m.         (real 2000-2026: -0.68 to 3.02)
    1: (0.5, 20.0),     # Dólar comercial      (real 2000-2026: 1.53 to 6.21)
}


class DataValidationError(Exception):
    """Raised when extracted data fails validation."""


BASE_SCHEMA = pa.DataFrameSchema(
    columns = {
        "series_id": pa.Column("int64", pa.Check.isin(list(VALID_RANGES))),
        "ref_date": pa.Column(
            "datetime64[ns]",
            pa.Check(lambda s: s <= pd.Timestamp.today(), error = "date in the future"),
        ),
        "value": pa.Column("float64", nullable = False),
    },
    checks = pa.Check(lambda df: df["series_id"].nunique() <= 1, error = "more than one series"),
    unique = ["series_id", "ref_date"],  # one value per series per day
    strict = True,                       # reject unexpected extra columns
    ordered = True,                      # columns must come in this order
)


def schema_for(series_id: int) -> pa.DataFrameSchema:
    """Base schema plus the value range for one series."""

    if series_id not in VALID_RANGES:
        return BASE_SCHEMA  # the isin check on series_id will report it
    
    low, high = VALID_RANGES[series_id]

    return BASE_SCHEMA.update_column("value", checks = [pa.Check.in_range(low, high)])


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Return df unchanged if valid; otherwise raise, listing every problem."""

    schema = BASE_SCHEMA if df.empty else schema_for(int(df["series_id"].iloc[0]))

    try:
        return schema.validate(df, lazy=True)  # lazy: collect ALL errors, not just the first
    
    except pa.errors.SchemaErrors as exc:
        cases = exc.failure_cases[["column", "check", "index", "failure_case"]]
        raise DataValidationError(
            f"{len(cases)} problem(s) found:\n{cases.head(20).to_string(index=False)}"
        ) from exc
