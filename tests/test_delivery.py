import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from report_autopilot.delivery import (
    send_email_report, send_slack_notification, send_anomaly_alert, deliver_report, DeliveryError,
)
from report_autopilot.client_config import ClientConfig, DeliveryConfig


@pytest.fixture
def fake_pdf(tmp_path):
    p = tmp_path / "report.pdf"
    p.write_bytes(b"%PDF-1.4 fake pdf content")
    return str(p)


# ---------- email ----------

def test_send_email_missing_recipients_raises(fake_pdf):
    with pytest.raises(DeliveryError, match="No recipient"):
        send_email_report(fake_pdf, [], "Acme Corp", smtp_host="x", smtp_user="u", smtp_password="p")


def test_send_email_missing_smtp_config_raises(fake_pdf):
    with pytest.raises(DeliveryError, match="SMTP is not configured"):
        send_email_report(fake_pdf, ["client@example.com"], "Acme Corp")


def test_send_email_missing_pdf_file_raises(tmp_path):
    with pytest.raises(DeliveryError, match="not found"):
        send_email_report(
            str(tmp_path / "does_not_exist.pdf"), ["client@example.com"], "Acme Corp",
            smtp_host="smtp.example.com", smtp_user="u", smtp_password="p",
        )


def test_send_email_success(fake_pdf):
    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = mock_server
        result = send_email_report(
            fake_pdf, ["client@example.com"], "Acme Corp",
            smtp_host="smtp.example.com", smtp_user="me@agency.com", smtp_password="secret",
        )
    assert result is True
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("me@agency.com", "secret")
    mock_server.sendmail.assert_called_once()


def test_send_email_smtp_failure_raises_delivery_error(fake_pdf):
    import smtplib
    with patch("smtplib.SMTP", side_effect=smtplib.SMTPException("auth failed")):
        with pytest.raises(DeliveryError, match="Failed to send email"):
            send_email_report(
                fake_pdf, ["client@example.com"], "Acme Corp",
                smtp_host="smtp.example.com", smtp_user="u", smtp_password="wrongpass",
            )


def test_send_email_connection_failure_raises_delivery_error(fake_pdf):
    with patch("smtplib.SMTP", side_effect=OSError("connection refused")):
        with pytest.raises(DeliveryError, match="Could not connect"):
            send_email_report(
                fake_pdf, ["client@example.com"], "Acme Corp",
                smtp_host="unreachable.invalid", smtp_user="u", smtp_password="p",
            )


# ---------- slack ----------

def test_send_slack_missing_webhook_raises(fake_pdf):
    with pytest.raises(DeliveryError, match="No Slack webhook"):
        send_slack_notification(None, "Acme Corp", fake_pdf)


def test_send_slack_success(fake_pdf):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    with patch("requests.post", return_value=mock_response) as mock_post:
        result = send_slack_notification("https://hooks.slack.com/fake", "Acme Corp", fake_pdf, "Revenue up 5%")
    assert result is True
    args, kwargs = mock_post.call_args
    assert "Acme Corp" in kwargs["json"]["text"]
    assert "Revenue up 5%" in kwargs["json"]["text"]


def test_send_slack_http_failure_raises_delivery_error(fake_pdf):
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("dns failure")):
        with pytest.raises(DeliveryError, match="Failed to post to Slack"):
            send_slack_notification("https://hooks.slack.com/fake", "Acme Corp", fake_pdf)


# ---------- anomaly alerts ----------

def test_send_anomaly_alert_missing_webhook_raises():
    from report_autopilot.anomaly_agent import AgentDecision, Severity
    decision = AgentDecision(overall_severity=Severity.CRITICAL, findings=[], should_alert_immediately=True)
    with pytest.raises(DeliveryError, match="No Slack webhook"):
        send_anomaly_alert(None, "Acme Corp", decision)


def test_send_anomaly_alert_success_includes_findings_in_message():
    from report_autopilot.anomaly_agent import AgentDecision, Finding, Severity
    decision = AgentDecision(
        overall_severity=Severity.CRITICAL,
        findings=[Finding(severity=Severity.CRITICAL, rule="roas_drop_critical", channel="Meta Ads",
                           message="Meta Ads ROAS dropped 70%.")],
        should_alert_immediately=True,
    )
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    with patch("requests.post", return_value=mock_response) as mock_post:
        result = send_anomaly_alert("https://hooks.slack.com/fake", "Acme Corp", decision)
    assert result is True
    args, kwargs = mock_post.call_args
    assert "Acme Corp" in kwargs["json"]["text"]
    assert "Meta Ads ROAS dropped 70%" in kwargs["json"]["text"]
    assert "CRITICAL" in kwargs["json"]["text"]


def test_send_anomaly_alert_http_failure_raises_delivery_error():
    from report_autopilot.anomaly_agent import AgentDecision, Severity
    decision = AgentDecision(overall_severity=Severity.ALERT, findings=[], should_alert_immediately=True)
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("dns failure")):
        with pytest.raises(DeliveryError, match="Failed to post anomaly alert"):
            send_anomaly_alert("https://hooks.slack.com/fake", "Acme Corp", decision)


# ---------- deliver_report (orchestration) ----------

def test_deliver_report_no_channels_configured_returns_empty(fake_pdf):
    config = ClientConfig(client_name="Acme Corp")  # no delivery config at all
    results = deliver_report(config, fake_pdf)
    assert results == {}


def test_deliver_report_partial_failure_does_not_raise(fake_pdf):
    """If Slack succeeds but email fails (or vice versa), deliver_report
    must report both outcomes, not raise and lose the successful one."""
    config = ClientConfig(
        client_name="Acme Corp",
        delivery=DeliveryConfig(email_to=["client@example.com"], slack_webhook_url="https://hooks.slack.com/fake"),
    )

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None

    with patch("smtplib.SMTP", side_effect=OSError("smtp down")), \
         patch("requests.post", return_value=mock_response):
        results = deliver_report(config, fake_pdf)

    assert results["email"].startswith("failed")
    assert results["slack"] == "sent"
