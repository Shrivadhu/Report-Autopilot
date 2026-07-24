"""
delivery.py
-----------
Sends a generated report out into the world -- email attachment and/or
a Slack notification with a link. This is the piece that was missing
before: generating a PDF locally isn't useful in production if a human
still has to remember to go find it and send it.

Design choice: delivery failures NEVER take down report generation.
The PDF is already built and saved to disk by the time delivery runs
-- if email or Slack fails, that's logged and surfaced, but the report
itself still exists and the run still exits successfully. Losing a
notification is recoverable (re-run delivery, or send manually);
silently losing the report itself would not be.
"""

import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

logger = logging.getLogger("report_autopilot.delivery")


class DeliveryError(Exception):
    pass


def send_email_report(
    pdf_path: str,
    to_addresses: list,
    client_name: str,
    smtp_host: str = None,
    smtp_port: int = 587,
    smtp_user: str = None,
    smtp_password: str = None,
) -> bool:
    """
    Emails the generated PDF as an attachment. Reads SMTP credentials
    from arguments, falling back to SMTP_HOST / SMTP_PORT / SMTP_USER /
    SMTP_PASSWORD environment variables if not passed explicitly.

    Returns True on success. Raises DeliveryError on failure (caller
    decides whether that should be fatal -- see cli.py, where it does
    not stop the run since the report itself is already saved).
    """
    smtp_host = smtp_host or os.environ.get("SMTP_HOST")
    smtp_user = smtp_user or os.environ.get("SMTP_USER")
    smtp_password = smtp_password or os.environ.get("SMTP_PASSWORD")

    if not to_addresses:
        raise DeliveryError("No recipient email addresses configured for this client.")
    if not (smtp_host and smtp_user and smtp_password):
        raise DeliveryError(
            "SMTP is not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD "
            "(env vars or arguments) before enabling email delivery."
        )
    if not os.path.exists(pdf_path):
        raise DeliveryError(f"Report file not found: {pdf_path}")

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_addresses)
    msg["Subject"] = f"{client_name} — Weekly Performance Report"
    msg.attach(MIMEText(
        f"Hi,\n\nAttached is the latest weekly performance report for {client_name}.\n\n"
        f"Best,\nReport Autopilot",
        "plain",
    ))

    with open(pdf_path, "rb") as f:
        part = MIMEApplication(f.read(), Name=os.path.basename(pdf_path))
    part["Content-Disposition"] = f'attachment; filename="{os.path.basename(pdf_path)}"'
    msg.attach(part)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_addresses, msg.as_string())
    except smtplib.SMTPException as e:
        raise DeliveryError(f"Failed to send email: {e}")
    except OSError as e:
        raise DeliveryError(f"Could not connect to SMTP server {smtp_host}:{smtp_port}: {e}")

    logger.info(f"Emailed report to {to_addresses}")
    return True


def send_slack_notification(
    webhook_url: str,
    client_name: str,
    pdf_path: str,
    summary_line: str = None,
) -> bool:
    """
    Posts a notification to a Slack incoming webhook. Slack webhooks
    can't attach files directly, so this sends a message with the
    report's filename/path -- pair it with a shared drive link in
    practice (see the note in README about the delivery step's scope).
    """
    if not webhook_url:
        raise DeliveryError("No Slack webhook URL configured for this client.")

    text = f"*{client_name}* weekly report is ready: `{os.path.basename(pdf_path)}`"
    if summary_line:
        text += f"\n{summary_line}"

    try:
        response = requests.post(webhook_url, json={"text": text}, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise DeliveryError(f"Failed to post to Slack: {e}")

    logger.info("Posted Slack notification")
    return True


def send_anomaly_alert(webhook_url: str, client_name: str, decision) -> bool:
    """
    Sends an immediate Slack alert for an anomaly agent decision --
    deliberately separate from send_slack_notification (which announces
    a finished report) because this fires independently of the weekly
    report cycle, on its own trigger (a CRITICAL/ALERT finding), with
    its own message shape built from the finding list rather than a
    report file path.
    """
    if not webhook_url:
        raise DeliveryError("No Slack webhook URL configured for this client.")

    lines = [f"⚠️ *{client_name}* — automated alert ({decision.overall_severity.value.upper()})"]
    for finding in decision.alert_findings:
        lines.append(f"• [{finding.rule}] {finding.message}")
    text = "\n".join(lines)

    try:
        response = requests.post(webhook_url, json={"text": text}, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise DeliveryError(f"Failed to post anomaly alert to Slack: {e}")

    logger.info(f"Posted anomaly alert to Slack for {client_name} ({decision.overall_severity.value})")
    return True


def deliver_report(client_config, pdf_path: str, comparison=None) -> dict:
    """
    Convenience wrapper: attempts every delivery channel configured for
    this client, and returns a per-channel success/failure summary
    instead of raising -- one failed channel should not be treated as
    the whole delivery step failing, since e.g. Slack succeeding while
    email fails is still partial value delivered, not total failure.
    """
    results = {}

    if client_config.delivery.email_to:
        try:
            send_email_report(pdf_path, client_config.delivery.email_to, client_config.client_name)
            results["email"] = "sent"
        except DeliveryError as e:
            logger.warning(f"Email delivery failed: {e}")
            results["email"] = f"failed: {e}"

    if client_config.delivery.slack_webhook_url:
        summary = None
        if comparison is not None:
            summary = f"Revenue: {comparison.current_totals.revenue:,.0f} this period."
        try:
            send_slack_notification(client_config.delivery.slack_webhook_url, client_config.client_name, pdf_path, summary)
            results["slack"] = "sent"
        except DeliveryError as e:
            logger.warning(f"Slack delivery failed: {e}")
            results["slack"] = f"failed: {e}"

    if not results:
        logger.info("No delivery channels configured for this client -- report saved locally only.")

    return results
