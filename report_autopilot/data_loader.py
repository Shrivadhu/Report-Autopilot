"""
data_loader.py
---------------
Loads raw campaign performance data and normalizes it into one common
schema, regardless of which platform it was exported from.

Real Google Ads / GA4 / Meta Ads Manager exports don't share column
names (e.g. Google Ads uses "Cost", Meta uses "Amount spent"), so any
tool meant to actually be used on real exports needs a mapping layer
instead of assuming one fixed format. This ships with mappings for the
three most common platforms plus a "generic" fallback -- add more
platforms by extending COLUMN_MAPS.
"""

import pandas as pd

REQUIRED_COLUMNS = ["date", "channel", "campaign", "impressions", "clicks", "cost", "conversions", "revenue"]

# Maps platform-specific export column names -> our normalized schema.
# Matching is case-insensitive and whitespace-tolerant.
COLUMN_MAPS = {
    "generic": {
        "date": "date", "channel": "channel", "campaign": "campaign",
        "impressions": "impressions", "clicks": "clicks", "cost": "cost",
        "conversions": "conversions", "revenue": "revenue",
    },
    "google_ads": {
        "day": "date", "campaign": "campaign", "impressions": "impressions",
        "clicks": "clicks", "cost": "cost", "conversions": "conversions",
        "conv. value": "revenue", "campaign type": "channel",
    },
    "meta_ads": {
        "reporting starts": "date", "campaign name": "campaign",
        "impressions": "impressions", "link clicks": "clicks",
        "amount spent (inr)": "cost", "amount spent (usd)": "cost",
        "results": "conversions", "purchase conversion value": "revenue",
    },
    "ga4": {
        "date": "date", "session default channel group": "channel",
        "session campaign name": "campaign", "sessions": "impressions",
        "engaged sessions": "clicks", "key events": "conversions",
        "total revenue": "revenue",
    },
}


class DataLoadError(Exception):
    pass


def _normalize_colname(c: str) -> str:
    return c.strip().lower()


def load_csv(path: str, platform: str = "generic") -> pd.DataFrame:
    """
    Load a campaign performance CSV and return a normalized DataFrame
    with columns: date, channel, campaign, impressions, clicks, cost,
    conversions, revenue.

    platform: one of "generic", "google_ads", "meta_ads", "ga4" -- picks
    which column-name mapping to apply. Use "generic" if your export
    already uses our column names (see sample_data/ for the format).
    """
    if platform not in COLUMN_MAPS:
        raise DataLoadError(
            f"Unknown platform {platform!r}. Supported: {list(COLUMN_MAPS)}"
        )

    df = pd.read_csv(path)
    df.columns = [_normalize_colname(c) for c in df.columns]

    mapping = {_normalize_colname(k): v for k, v in COLUMN_MAPS[platform].items()}
    df = df.rename(columns=mapping)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataLoadError(
            f"After mapping for platform={platform!r}, still missing columns: "
            f"{missing}. Available columns were: {list(df.columns)}. "
            f"If this is a new export format, add a mapping to COLUMN_MAPS."
        )

    df = df[REQUIRED_COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if df["date"].isna().any():
        raise DataLoadError("Some rows have an unparseable date -- check the source file.")

    for col in ["impressions", "clicks", "cost", "conversions", "revenue"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["channel"] = df["channel"].fillna("Unknown")
    return df.sort_values("date").reset_index(drop=True)
