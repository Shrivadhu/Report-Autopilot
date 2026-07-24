import subprocess
import sys
import os
import json

from pypdf import PdfReader


def _isolated_env(tmp_path, monkeypatch):
    """Every subprocess CLI test must run with its own ledger AND intake
    store -- otherwise 'Test Client' runs leak into the real production
    efficiency_ledger.json / intake_opportunities.json that the Leverage
    Dashboard and Automation Intake tabs report from. This was a real
    bug caught during manual review (see efficiency_ledger.py and
    intake.py's env-var override)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = os.environ.copy()
    env["REPORT_AUTOPILOT_LEDGER_PATH"] = str(tmp_path / "test_ledger.json")
    env["REPORT_AUTOPILOT_INTAKE_PATH"] = str(tmp_path / "test_intake.json")
    return env


def test_cli_generates_valid_pdf(tmp_path, monkeypatch):
    env = _isolated_env(tmp_path, monkeypatch)
    output_path = tmp_path / "test_report.pdf"

    result = subprocess.run(
        [
            sys.executable, "-m", "report_autopilot.cli",
            "--data", "sample_data/sample_campaign_data.csv",
            "--client", "Test Client",
            "--offline",
            "--output", str(output_path),
        ],
        capture_output=True, text=True, env=env,
    )

    assert result.returncode == 0, result.stderr
    assert output_path.exists()

    reader = PdfReader(str(output_path))
    assert len(reader.pages) >= 1


def test_cli_falls_back_gracefully_without_api_key(tmp_path, monkeypatch):
    """This is the exact production-safety property that matters most:
    if the API key is missing or the API call fails, the CLI must still
    exit 0 and produce a report, not crash."""
    env = _isolated_env(tmp_path, monkeypatch)
    output_path = tmp_path / "fallback_report.pdf"

    result = subprocess.run(
        [
            sys.executable, "-m", "report_autopilot.cli",
            "--data", "sample_data/sample_campaign_data.csv",
            "--client", "Test Client",
            "--output", str(output_path),
        ],
        capture_output=True, text=True, env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "Falling back" in result.stderr
    assert output_path.exists()


def test_cli_exits_nonzero_on_bad_data(tmp_path, monkeypatch):
    env = _isolated_env(tmp_path, monkeypatch)
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("foo,bar\n1,2\n")

    result = subprocess.run(
        [
            sys.executable, "-m", "report_autopilot.cli",
            "--data", str(bad_csv),
            "--client", "Test Client",
            "--offline",
        ],
        capture_output=True, text=True, env=env,
    )

    assert result.returncode == 1
    assert "missing columns" in result.stderr


def test_cli_logs_run_to_isolated_ledger_not_production(tmp_path, monkeypatch):
    """Regression test for the ledger-pollution bug: confirms a CLI run
    writes to the isolated test ledger, and that path is genuinely
    separate from the default production path."""
    env = _isolated_env(tmp_path, monkeypatch)
    ledger_path = env["REPORT_AUTOPILOT_LEDGER_PATH"]
    output_path = tmp_path / "report.pdf"

    result = subprocess.run(
        [
            sys.executable, "-m", "report_autopilot.cli",
            "--data", "sample_data/sample_campaign_data.csv",
            "--client", "Isolated Test Client",
            "--offline",
            "--output", str(output_path),
        ],
        capture_output=True, text=True, env=env,
    )

    assert result.returncode == 0, result.stderr
    assert os.path.exists(ledger_path)
    with open(ledger_path) as f:
        entries = json.load(f)
    assert len(entries) == 1
    assert entries[0]["client"] == "Isolated Test Client"

    # And confirm this did NOT touch the real default path
    assert ledger_path != "data/efficiency_ledger.json"


def test_cli_anomalous_data_logs_to_isolated_intake_not_production(tmp_path, monkeypatch):
    """Regression test using genuinely CRITICAL-severity data through
    the real CLI subprocess (not a unit test of the agent in isolation)
    -- confirms the self-feeding intake loop writes to the isolated
    test path, never the real production intake queue."""
    env = _isolated_env(tmp_path, monkeypatch)
    intake_path = env["REPORT_AUTOPILOT_INTAKE_PATH"]

    anomalous_csv = tmp_path / "anomalous.csv"
    anomalous_csv.write_text(
        "date,channel,campaign,impressions,clicks,cost,conversions,revenue\n"
        "2026-06-15,Meta Ads,Retargeting,40000,800,1200,50,6000\n"
        "2026-06-22,Meta Ads,Retargeting,40000,800,1200,50,6000\n"
        "2026-06-29,Meta Ads,Retargeting,40000,800,1200,5,600\n"
    )

    result = subprocess.run(
        [
            sys.executable, "-m", "report_autopilot.cli",
            "--data", str(anomalous_csv),
            "--client", "Isolated Anomaly Test",
            "--offline",
            "--output", str(tmp_path / "report.pdf"),
        ],
        capture_output=True, text=True, env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "CRITICAL" in result.stderr
    assert os.path.exists(intake_path)

    with open(intake_path) as f:
        entries = json.load(f)
    assert len(entries) >= 1
    assert all("Isolated Anomaly Test" in e["department"] for e in entries)

    # And confirm this did NOT touch the real default path
    assert intake_path != "data/intake_opportunities.json"
