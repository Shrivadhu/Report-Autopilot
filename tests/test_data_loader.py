import pandas as pd
import pytest

from report_autopilot.data_loader import load_csv, DataLoadError


def test_load_generic_csv_ok():
    df = load_csv("sample_data/sample_campaign_data.csv", platform="generic")
    assert len(df) == 30
    assert list(df.columns) == [
        "date", "channel", "campaign", "impressions", "clicks",
        "cost", "conversions", "revenue",
    ]
    assert df["date"].dtype.kind == "M"  # datetime


def test_load_missing_columns_raises(tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("foo,bar\n1,2\n")
    with pytest.raises(DataLoadError, match="missing columns"):
        load_csv(str(bad_csv), platform="generic")


def test_load_unknown_platform_raises():
    with pytest.raises(DataLoadError, match="Unknown platform"):
        load_csv("sample_data/sample_campaign_data.csv", platform="not_a_real_platform")


def test_load_bad_dates_raise(tmp_path):
    csv = tmp_path / "bad_dates.csv"
    csv.write_text(
        "date,channel,campaign,impressions,clicks,cost,conversions,revenue\n"
        "not-a-date,Google Ads,Brand,100,10,5,1,20\n"
    )
    with pytest.raises(DataLoadError, match="unparseable date"):
        load_csv(str(csv), platform="generic")


def test_google_ads_platform_mapping(tmp_path):
    csv = tmp_path / "google_export.csv"
    csv.write_text(
        "Day,Campaign,Campaign type,Impressions,Clicks,Cost,Conversions,Conv. value\n"
        "2026-01-01,Brand Search,Search,1000,50,25.00,3,150.00\n"
    )
    df = load_csv(str(csv), platform="google_ads")
    assert len(df) == 1
    assert df.iloc[0]["revenue"] == 150.00
    assert df.iloc[0]["channel"] == "Search"


def test_missing_numeric_values_default_to_zero(tmp_path):
    csv = tmp_path / "partial.csv"
    csv.write_text(
        "date,channel,campaign,impressions,clicks,cost,conversions,revenue\n"
        "2026-01-01,Google Ads,Brand,,10,5,,20\n"
    )
    df = load_csv(str(csv), platform="generic")
    assert df.iloc[0]["impressions"] == 0
    assert df.iloc[0]["conversions"] == 0
