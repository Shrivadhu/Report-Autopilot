"""
metrics.py
----------
Turns raw row-level data into the comparison numbers a client report
actually needs: this period vs. last period, broken down overall and
by channel, plus derived marketing KPIs (CTR, CPC, CPA, ROAS) that
don't exist in the raw export and have to be computed.
"""

from dataclasses import dataclass, field
import pandas as pd


@dataclass
class ChannelSummary:
    channel: str
    impressions: float
    clicks: float
    cost: float
    conversions: float
    revenue: float

    @property
    def ctr(self):
        return (self.clicks / self.impressions * 100) if self.impressions else 0.0

    @property
    def cpc(self):
        return (self.cost / self.clicks) if self.clicks else 0.0

    @property
    def cpa(self):
        return (self.cost / self.conversions) if self.conversions else 0.0

    @property
    def roas(self):
        return (self.revenue / self.cost) if self.cost else 0.0


@dataclass
class PeriodComparison:
    current_start: pd.Timestamp
    current_end: pd.Timestamp
    previous_start: pd.Timestamp
    previous_end: pd.Timestamp
    current_totals: ChannelSummary
    previous_totals: ChannelSummary
    current_by_channel: list       # list[ChannelSummary]
    previous_by_channel: list      # list[ChannelSummary]
    biggest_gainer: dict = field(default_factory=dict)   # {channel, metric, pct_change}
    biggest_decliner: dict = field(default_factory=dict)


def _summarize(df: pd.DataFrame, channel: str = "TOTAL") -> ChannelSummary:
    return ChannelSummary(
        channel=channel,
        impressions=df["impressions"].sum(),
        clicks=df["clicks"].sum(),
        cost=df["cost"].sum(),
        conversions=df["conversions"].sum(),
        revenue=df["revenue"].sum(),
    )


def _by_channel(df: pd.DataFrame) -> list:
    return [_summarize(g, channel=name) for name, g in df.groupby("channel")]


def pct_change(old, new):
    if old == 0:
        return None  # undefined -- avoid a misleading "infinite%" claim
    return (new - old) / old * 100


def compare_periods(df: pd.DataFrame, period_days: int = 7) -> PeriodComparison:
    """
    Splits the most recent `period_days` of data as "current", and the
    `period_days` immediately before that as "previous", then
    aggregates both overall and per-channel.
    """
    if df.empty:
        raise ValueError("no data to summarize")

    max_date = df["date"].max()
    current_start = max_date - pd.Timedelta(days=period_days - 1)
    previous_end = current_start - pd.Timedelta(days=1)
    previous_start = previous_end - pd.Timedelta(days=period_days - 1)

    current_df = df[(df["date"] >= current_start) & (df["date"] <= max_date)]
    previous_df = df[(df["date"] >= previous_start) & (df["date"] <= previous_end)]

    current_totals = _summarize(current_df)
    previous_totals = _summarize(previous_df)
    current_by_channel = _by_channel(current_df)
    previous_by_channel = _by_channel(previous_df)

    comparison = PeriodComparison(
        current_start=current_start, current_end=max_date,
        previous_start=previous_start, previous_end=previous_end,
        current_totals=current_totals, previous_totals=previous_totals,
        current_by_channel=current_by_channel,
        previous_by_channel=previous_by_channel,
    )
    comparison.biggest_gainer, comparison.biggest_decliner = _find_movers(
        current_by_channel, previous_by_channel
    )
    return comparison


def _find_movers(current_by_channel, previous_by_channel):
    """Finds the channel with the largest revenue % swing in each direction.
    Channels with no prior spend (pct_change=None) are excluded, since a
    'new channel' isn't a meaningful mover to report."""
    prev_by_name = {c.channel: c for c in previous_by_channel}
    moves = []
    for cur in current_by_channel:
        prev = prev_by_name.get(cur.channel)
        if prev is None:
            continue
        change = pct_change(prev.revenue, cur.revenue)
        if change is None:
            continue
        moves.append((cur.channel, change))

    if not moves:
        return {}, {}

    gainer = max(moves, key=lambda x: x[1])
    decliner = min(moves, key=lambda x: x[1])
    return (
        {"channel": gainer[0], "metric": "revenue", "pct_change": gainer[1]},
        {"channel": decliner[0], "metric": "revenue", "pct_change": decliner[1]},
    )
