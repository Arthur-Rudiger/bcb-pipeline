"""Central configuration. Override any value with environment variables."""
import os
from datetime import date

DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://bcb:bcb@localhost:5432/bcb")

BACKFILL_START = date(2000, 1, 1)   # first run fetches from here
LOOKBACK_DAYS = 45                  # later runs re-fetch this many days to catch revisions
