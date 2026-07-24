"""
connectors/base.py
-------------------
Interface for pulling data directly from an ad platform's API instead
of requiring a manual CSV export. This is intentionally an interface +
one honestly-labeled stub, not a full working OAuth integration --
each platform (Google Ads, Meta, GA4) has its own OAuth flow, token
refresh handling, and rate limits, and building all three correctly is
a multi-day task per platform that needs real API credentials to test
against, which this environment doesn't have.

What IS real here: the shape every connector must implement, so
`data_loader.py`'s normalized schema stays the single source of truth
regardless of whether data arrived via CSV or a live API pull -- a new
connector only needs to produce a DataFrame with the same columns
load_csv() already produces.
"""

from abc import ABC, abstractmethod
import pandas as pd

REQUIRED_COLUMNS = ["date", "channel", "campaign", "impressions", "clicks", "cost", "conversions", "revenue"]


class ConnectorError(Exception):
    pass


class DataConnector(ABC):
    """
    Every live-pull connector (Google Ads, Meta Ads, GA4, ...) should
    subclass this and implement fetch(). The rest of the pipeline
    (metrics.py, analyzer.py, report_builder.py) doesn't care where the
    DataFrame came from, as long as it has REQUIRED_COLUMNS.
    """

    @abstractmethod
    def fetch(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Returns a DataFrame with REQUIRED_COLUMNS for the given date range."""
        raise NotImplementedError

    def validate(self, df: pd.DataFrame):
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ConnectorError(
                f"{type(self).__name__}.fetch() returned a DataFrame missing "
                f"columns: {missing}. Every connector must produce exactly "
                f"the schema in REQUIRED_COLUMNS."
            )
