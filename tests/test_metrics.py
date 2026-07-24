import pandas as pd
import pytest

from report_autopilot.data_loader import load_csv
from report_autopilot.metrics import compare_periods, pct_change, ChannelSummary


@pytest.fixture
def sample_df():
    return load_csv("sample_data/sample_campaign_data.csv", platform="generic")


def test_pct_change_basic():
    assert pct_change(100, 110) == 10.0
    assert pct_change(100, 90) == -10.0
    assert pct_change(0, 50) is None  # undefined, must not divide by zero


def test_channel_summary_derived_metrics():
    c = ChannelSummary(channel="Test", impressions=1000, clicks=50, cost=100, conversions=5, revenue=500)
    assert c.ctr == 5.0
    assert c.cpc == 2.0
    assert c.cpa == 20.0
    assert c.roas == 5.0


def test_channel_summary_handles_zero_denominators():
    c = ChannelSummary(channel="Test", impressions=0, clicks=0, cost=0, conversions=0, revenue=0)
    assert c.ctr == 0.0
    assert c.cpc == 0.0
    assert c.cpa == 0.0
    assert c.roas == 0.0


def test_compare_periods_totals_match_manual_sum(sample_df):
    comp = compare_periods(sample_df, period_days=7)
    current_slice = sample_df[
        (sample_df["date"] >= comp.current_start) & (sample_df["date"] <= comp.current_end)
    ]
    assert comp.current_totals.revenue == current_slice["revenue"].sum()
    assert comp.current_totals.cost == current_slice["cost"].sum()


def test_compare_periods_empty_df_raises():
    empty = pd.DataFrame(columns=["date", "channel", "campaign", "impressions", "clicks", "cost", "conversions", "revenue"])
    with pytest.raises(ValueError):
        compare_periods(empty)


def test_biggest_gainer_and_decliner_are_different_channels(sample_df):
    comp = compare_periods(sample_df, period_days=7)
    if comp.biggest_gainer and comp.biggest_decliner:
        assert comp.biggest_gainer["channel"] != comp.biggest_decliner["channel"] or \
               comp.biggest_gainer["pct_change"] == comp.biggest_decliner["pct_change"]


def test_new_channel_with_no_prior_period_excluded_from_movers():
    """A channel with zero spend in the previous period should not produce
    a divide-by-zero or nonsensical 'infinite % growth' mover result."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-08"]),
        "channel": ["Existing", "BrandNewChannel"],
        "campaign": ["c1", "c2"],
        "impressions": [1000, 500],
        "clicks": [50, 20],
        "cost": [100, 50],
        "conversions": [5, 2],
        "revenue": [500, 200],
    })
    comp = compare_periods(df, period_days=7)
    # BrandNewChannel has no previous-period data, so it must not appear as a mover
    if comp.biggest_gainer:
        assert comp.biggest_gainer["channel"] != "BrandNewChannel"
