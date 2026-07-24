import pytest

from report_autopilot.efficiency_ledger import EfficiencyLedger, ESTIMATED_MINUTES_SAVED_PER_RUN


@pytest.fixture
def ledger(tmp_path):
    return EfficiencyLedger(path=str(tmp_path / "ledger.json"))


def test_log_run_records_entry(ledger):
    entry = ledger.log_run(client="Acme Corp")
    assert entry.client == "Acme Corp"
    assert entry.minutes_saved_estimate == ESTIMATED_MINUTES_SAVED_PER_RUN
    assert entry.timestamp  # auto-populated


def test_log_run_persists_across_instances(tmp_path):
    path = str(tmp_path / "shared_ledger.json")
    ledger1 = EfficiencyLedger(path=path)
    ledger1.log_run(client="Acme Corp")

    ledger2 = EfficiencyLedger(path=path)
    assert len(ledger2.all_entries()) == 1


def test_summarize_empty_ledger(ledger):
    summary = ledger.summarize()
    assert summary["total_runs"] == 0
    assert summary["total_hours_saved"] == 0
    assert summary["total_estimated_value_usd"] == 0


def test_summarize_totals_are_correct(ledger):
    ledger.log_run(client="Acme Corp", minutes_saved=110)
    ledger.log_run(client="Acme Corp", minutes_saved=110)
    ledger.log_run(client="Beta Inc", minutes_saved=90)

    summary = ledger.summarize(hourly_cost_usd=50)
    assert summary["total_runs"] == 3
    assert summary["total_hours_saved"] == round((110 + 110 + 90) / 60, 1)
    expected_value = round(((110 + 110 + 90) / 60) * 50, 2)
    assert summary["total_estimated_value_usd"] == expected_value


def test_summarize_breaks_down_by_client(ledger):
    ledger.log_run(client="Acme Corp", minutes_saved=110)
    ledger.log_run(client="Beta Inc", minutes_saved=60)

    summary = ledger.summarize()
    assert summary["hours_saved_by_client"]["Acme Corp"] == round(110 / 60, 1)
    assert summary["hours_saved_by_client"]["Beta Inc"] == round(60 / 60, 1)


def test_summarize_breaks_down_by_automation(ledger):
    ledger.log_run(client="Acme Corp", automation="report_autopilot", minutes_saved=110)
    ledger.log_run(client="Acme Corp", automation="lead_qualifier", minutes_saved=30)

    summary = ledger.summarize()
    assert "report_autopilot" in summary["hours_saved_by_automation"]
    assert "lead_qualifier" in summary["hours_saved_by_automation"]
