"""Times with a time zone (WP10).

Before WP10 most time columns were ``timestamp without time zone``: a bare
clock reading with no zone. Python wrote UTC into them, but a database or a
browser set to Kenyan time read them as Kenyan time, so times showed 3 hours
off. Three things now make every time unambiguous:

* every database connection runs in UTC (``app.core.database``);
* migration 039 turns every such column into ``timestamptz`` (a real moment in
  time), reading the stored values as UTC;
* the API writes every time with its zone (``...Z``), so the browser shows it
  in the viewer's local time (East Africa Time in Kenya).

``UTCDateTime`` keeps the application code unchanged: it stores a
``timestamptz`` and hands Python the same "naive UTC" values it always used.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """``timestamptz`` in the database; naive UTC ``datetime`` in Python."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value


def iso_utc(value: datetime) -> str:
    """ISO 8601 with the zone. A time without a zone is UTC by convention."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def naive_utc_frame(df):
    """pandas: turn zone-aware time columns into naive UTC, as they were
    before the columns gained a zone (the analytics code compares them with
    naive values)."""
    import pandas as pd
    for col in df.columns:
        if isinstance(df[col].dtype, pd.DatetimeTZDtype):
            df[col] = df[col].dt.tz_convert("UTC").dt.tz_localize(None)
    return df


def install_json_encoder() -> None:
    """Make FastAPI write every ``datetime`` as UTC with a ``Z``."""
    import fastapi.encoders as enc
    enc.ENCODERS_BY_TYPE[datetime] = iso_utc
