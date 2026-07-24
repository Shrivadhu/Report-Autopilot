import json
import os

import pytest

from report_autopilot.webhook_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_generate_report_missing_fields_returns_400(client):
    resp = client.post("/generate-report", json={})
    assert resp.status_code == 400
    assert "required" in resp.get_json()["error"]


def test_generate_report_unknown_platform_returns_400(client):
    resp = client.post("/generate-report", json={
        "client_name": "Acme Corp",
        "data_path": "sample_data/sample_campaign_data.csv",
        "platform": "not_a_real_platform",
    })
    assert resp.status_code == 400
    assert "unknown platform" in resp.get_json()["error"]


def test_generate_report_bad_data_path_returns_400(client, tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("foo,bar\n1,2\n")
    resp = client.post("/generate-report", json={
        "client_name": "Acme Corp",
        "data_path": str(bad_csv),
        "offline": True,
    })
    assert resp.status_code == 400
    assert "data load failed" in resp.get_json()["error"]


def test_generate_report_success_offline(client, tmp_path, monkeypatch):
    monkeypatch.setenv("REPORT_AUTOPILOT_LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("REPORT_AUTOPILOT_INTAKE_PATH", str(tmp_path / "intake.json"))
    resp = client.post("/generate-report", json={
        "client_name": "Webhook Test Client",
        "data_path": "sample_data/sample_campaign_data.csv",
        "offline": True,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert os.path.exists(body["report_path"])
    assert body["anomaly_severity"] in ("ok", "watch", "alert", "critical")
    os.remove(body["report_path"])


def test_generate_report_logs_to_ledger(client, tmp_path, monkeypatch):
    ledger_path = str(tmp_path / "webhook_ledger.json")
    monkeypatch.setenv("REPORT_AUTOPILOT_LEDGER_PATH", ledger_path)
    monkeypatch.setenv("REPORT_AUTOPILOT_INTAKE_PATH", str(tmp_path / "intake.json"))

    resp = client.post("/generate-report", json={
        "client_name": "Ledger Test Client",
        "data_path": "sample_data/sample_campaign_data.csv",
        "offline": True,
    })
    assert resp.status_code == 200

    with open(ledger_path) as f:
        entries = json.load(f)
    assert len(entries) == 1
    assert entries[0]["client"] == "Ledger Test Client"
    assert entries[0]["automation"] == "webhook_trigger"

    os.remove(resp.get_json()["report_path"])


def test_auth_required_when_secret_is_set(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "my-test-secret")
    resp = client.post("/generate-report", json={
        "client_name": "Acme Corp",
        "data_path": "sample_data/sample_campaign_data.csv",
        "offline": True,
    })
    assert resp.status_code == 401


def test_auth_succeeds_with_correct_secret(client, monkeypatch, tmp_path):
    monkeypatch.setenv("WEBHOOK_SECRET", "my-test-secret")
    monkeypatch.setenv("REPORT_AUTOPILOT_LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("REPORT_AUTOPILOT_INTAKE_PATH", str(tmp_path / "intake.json"))
    resp = client.post(
        "/generate-report",
        json={"client_name": "Acme Corp", "data_path": "sample_data/sample_campaign_data.csv", "offline": True},
        headers={"X-Webhook-Secret": "my-test-secret"},
    )
    assert resp.status_code == 200
    os.remove(resp.get_json()["report_path"])


def test_auth_disabled_and_logged_when_no_secret_configured(client, monkeypatch, tmp_path, caplog):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("REPORT_AUTOPILOT_LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("REPORT_AUTOPILOT_INTAKE_PATH", str(tmp_path / "intake.json"))
    resp = client.post("/generate-report", json={
        "client_name": "Acme Corp", "data_path": "sample_data/sample_campaign_data.csv", "offline": True,
    })
    assert resp.status_code == 200
    assert "auth is DISABLED" in caplog.text
    os.remove(resp.get_json()["report_path"])


def test_anomalous_data_via_webhook_logs_to_isolated_intake(client, tmp_path, monkeypatch):
    """Same regression as the CLI version, but through the webhook
    trigger path -- confirms the self-feeding loop is wired in there
    too, and writes to isolated storage, never production."""
    ledger_path = str(tmp_path / "ledger.json")
    intake_path = str(tmp_path / "intake.json")
    monkeypatch.setenv("REPORT_AUTOPILOT_LEDGER_PATH", ledger_path)
    monkeypatch.setenv("REPORT_AUTOPILOT_INTAKE_PATH", intake_path)

    anomalous_csv = tmp_path / "anomalous.csv"
    anomalous_csv.write_text(
        "date,channel,campaign,impressions,clicks,cost,conversions,revenue\n"
        "2026-06-15,Meta Ads,Retargeting,40000,800,1200,50,6000\n"
        "2026-06-22,Meta Ads,Retargeting,40000,800,1200,50,6000\n"
        "2026-06-29,Meta Ads,Retargeting,40000,800,1200,5,600\n"
    )

    resp = client.post("/generate-report", json={
        "client_name": "Webhook Anomaly Test",
        "data_path": str(anomalous_csv),
        "offline": True,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["anomaly_severity"] == "critical"
    os.remove(body["report_path"])

    with open(intake_path) as f:
        entries = json.load(f)
    assert len(entries) >= 1
    assert all("Webhook Anomaly Test" in e["department"] for e in entries)
    assert intake_path != "data/intake_opportunities.json"
