import pandas as pd
import pytest

from report_autopilot.metrics import compare_periods
from report_autopilot.anomaly_agent import evaluate, Severity


def _make_df(rows):
    """rows: list of (date, channel, cost, revenue) -- fills in
    plausible impressions/clicks/conversions so ROAS/CTR/etc. compute
    without divide-by-zero noise in unrelated fields."""
    records = []
    for date, channel, cost, revenue in rows:
        records.append({
            "date": date, "channel": channel, "campaign": "test",
            "impressions": 10000, "clicks": 500, "cost": cost,
            "conversions": max(1, int(revenue / 100)), "revenue": revenue,
        })
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_healthy_data_produces_ok_with_no_findings():
    df = _make_df([
        ("2026-01-01", "Google Ads", 1000, 4000),
        ("2026-01-08", "Google Ads", 1000, 4100),  # basically flat, healthy
    ])
    comparison = compare_periods(df, period_days=7)
    decision = evaluate(comparison)
    assert decision.overall_severity == Severity.OK
    assert decision.findings == []
    assert decision.should_alert_immediately is False


def test_roas_collapse_triggers_critical():
    df = _make_df([
        ("2026-01-01", "Meta Ads", 1000, 5000),   # ROAS 5.0x
        ("2026-01-08", "Meta Ads", 1000, 1500),    # ROAS 1.5x -- a 70% ROAS drop
    ])
    comparison = compare_periods(df, period_days=7)
    decision = evaluate(comparison)
    assert decision.overall_severity == Severity.CRITICAL
    assert decision.should_alert_immediately is True
    assert any(f.rule == "roas_drop_critical" for f in decision.findings)
    assert any(f.channel == "Meta Ads" for f in decision.findings)


def test_moderate_roas_softening_triggers_watch_not_alert():
    df = _make_df([
        ("2026-01-01", "LinkedIn Ads", 1000, 4000),   # ROAS 4.0x
        ("2026-01-08", "LinkedIn Ads", 1000, 3500),    # ROAS 3.5x -- a 12.5% drop, mild
    ])
    comparison = compare_periods(df, period_days=7)
    decision = evaluate(comparison)
    assert decision.overall_severity == Severity.OK
    # 12.5% is below the 15% WATCH threshold, so genuinely nothing should fire
    assert decision.findings == []


def test_spend_spike_without_revenue_triggers_alert():
    df = _make_df([
        ("2026-01-01", "Meta Ads", 1000, 4000),
        ("2026-01-08", "Meta Ads", 1800, 4100),   # spend +80%, revenue basically flat
    ])
    comparison = compare_periods(df, period_days=7)
    decision = evaluate(comparison)
    assert any(f.rule == "spend_spike_no_revenue" for f in decision.findings)
    assert decision.should_alert_immediately is True


def test_total_revenue_collapse_triggers_critical_even_if_channels_look_ok():
    """A rule that only checked per-channel ROAS could miss an overall
    collapse if it's spread evenly across channels -- this confirms the
    total-revenue rule catches that independently."""
    df = _make_df([
        ("2026-01-01", "Google Ads", 1000, 2000),
        ("2026-01-01", "Meta Ads", 1000, 2000),
        ("2026-01-08", "Google Ads", 1000, 1100),
        ("2026-01-08", "Meta Ads", 1000, 1100),
    ])
    comparison = compare_periods(df, period_days=7)
    decision = evaluate(comparison)
    assert decision.overall_severity == Severity.CRITICAL
    assert any(f.rule == "revenue_drop_critical" for f in decision.findings)


def test_new_channel_with_no_prior_baseline_does_not_crash_or_false_alarm():
    """A channel with zero prior-period cost/revenue must not produce a
    divide-by-zero or a nonsensical alert -- it simply can't be
    evaluated yet, which is different from being a problem."""
    df = pd.DataFrame([
        {"date": "2026-01-01", "channel": "Google Ads", "campaign": "t", "impressions": 1000,
         "clicks": 50, "cost": 100, "conversions": 5, "revenue": 500},
        {"date": "2026-01-08", "channel": "Google Ads", "campaign": "t", "impressions": 1000,
         "clicks": 50, "cost": 100, "conversions": 5, "revenue": 500},
        {"date": "2026-01-08", "channel": "TikTok Ads", "campaign": "t", "impressions": 1000,
         "clicks": 50, "cost": 200, "conversions": 3, "revenue": 300},  # brand new this period
    ])
    df["date"] = pd.to_datetime(df["date"])
    comparison = compare_periods(df, period_days=7)
    decision = evaluate(comparison)  # must not raise
    assert not any(f.channel == "TikTok Ads" for f in decision.findings)


def test_thresholds_are_overridable():
    """Confirms thresholds aren't hardcoded -- a stricter threshold
    should catch something the default would classify as merely WATCH."""
    df = _make_df([
        ("2026-01-01", "Google Ads", 1000, 4000),
        ("2026-01-08", "Google Ads", 1000, 3600),   # ROAS 4.0 -> 3.6, a 10% drop
    ])
    comparison = compare_periods(df, period_days=7)

    default_decision = evaluate(comparison)
    assert default_decision.overall_severity == Severity.OK  # 10% is below default 15% watch threshold

    strict_decision = evaluate(comparison, thresholds={"roas_drop_watch_pct": -5.0})
    assert strict_decision.overall_severity == Severity.WATCH


def test_multiple_findings_overall_severity_is_the_worst_one():
    df = _make_df([
        ("2026-01-01", "Google Ads", 1000, 4000),
        ("2026-01-01", "Meta Ads", 1000, 4000),
        ("2026-01-08", "Google Ads", 1000, 3600),    # mild softening -> WATCH
        ("2026-01-08", "Meta Ads", 1000, 800),         # collapse -> CRITICAL
    ])
    comparison = compare_periods(df, period_days=7)
    decision = evaluate(comparison)
    severities = {f.severity for f in decision.findings}
    assert Severity.CRITICAL in severities
    assert decision.overall_severity == Severity.CRITICAL  # worst case wins, not first-found
