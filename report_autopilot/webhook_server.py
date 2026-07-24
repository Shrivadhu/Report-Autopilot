"""
webhook_server.py
------------------
Everything built so far is triggered by a human (CLI) or a schedule
(cron). This is the third trigger the JD names explicitly: "workflow
tools (Zapier, Make, custom scripts)." Zapier/Make can't run Python --
they call HTTP endpoints. This exposes the exact same tested pipeline
as a webhook, so a Zapier "when a new file lands in this Drive folder"
trigger, or a Slack slash command, or a Make.com scenario, can kick off
report generation without anyone touching a terminal.

Run with:
    python -m report_autopilot.webhook_server

Then, e.g. from Zapier's "Webhooks by Zapier" action, or curl:
    curl -X POST http://localhost:5000/generate-report \\
      -H "Content-Type: application/json" \\
      -d '{"client_name": "Acme Corp", "data_path": "sample_data/sample_campaign_data.csv", "offline": true}'

Auth: a shared-secret header (WEBHOOK_SECRET env var), not OAuth --
proportionate to an internal trigger endpoint, not a public API.
Documented as a real limitation in README, not hidden.
"""

import logging
import os

from flask import Flask, request, jsonify

from report_autopilot.data_loader import load_csv, DataLoadError, COLUMN_MAPS
from report_autopilot.metrics import compare_periods
from report_autopilot.charts import weekly_trend_chart, channel_breakdown_chart
from report_autopilot.analyzer import generate_narrative, generate_narrative_offline, AnalyzerError
from report_autopilot.report_builder import build_report
from report_autopilot.client_config import default_config
from report_autopilot.efficiency_ledger import EfficiencyLedger
from report_autopilot.intake import IntakeStore
from report_autopilot.anomaly_agent import evaluate as evaluate_anomalies
from report_autopilot.delivery import send_anomaly_alert, DeliveryError
from report_autopilot.logging_config import setup_logging

logger = logging.getLogger("report_autopilot.webhook")

app = Flask(__name__)


def _check_auth(req) -> bool:
    """Shared-secret check. If WEBHOOK_SECRET isn't set, auth is
    disabled (local/dev use) -- but that's logged loudly, not silent,
    since shipping that by accident to a public endpoint would be a
    real security gap."""
    secret = os.environ.get("WEBHOOK_SECRET")
    if not secret:
        logger.warning("WEBHOOK_SECRET is not set -- webhook auth is DISABLED. Do not expose this publicly like this.")
        return True
    return req.headers.get("X-Webhook-Secret") == secret


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/generate-report", methods=["POST"])
def generate_report_webhook():
    if not _check_auth(request):
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    client_name = payload.get("client_name")
    data_path = payload.get("data_path")
    platform = payload.get("platform", "generic")
    period_days = payload.get("period_days", 7)
    offline = payload.get("offline", False)

    if not client_name or not data_path:
        return jsonify({"error": "client_name and data_path are required"}), 400
    if platform not in COLUMN_MAPS:
        return jsonify({"error": f"unknown platform {platform!r}. Options: {list(COLUMN_MAPS)}"}), 400

    try:
        df = load_csv(data_path, platform=platform)
    except DataLoadError as e:
        return jsonify({"error": f"data load failed: {e}"}), 400

    try:
        comparison = compare_periods(df, period_days=period_days)
    except ValueError as e:
        return jsonify({"error": f"metrics computation failed: {e}"}), 400

    client_config = default_config(client_name)

    decision = evaluate_anomalies(comparison)
    alert_sent = False

    for finding in decision.alert_findings:
        new_opp = IntakeStore().add_from_finding(client_name, finding)
        if new_opp:
            logger.info(f"Auto-logged intake opportunity {new_opp.id} from webhook trigger: {new_opp.description}")

    if decision.should_alert_immediately and client_config.delivery.slack_webhook_url:
        try:
            send_anomaly_alert(client_config.delivery.slack_webhook_url, client_name, decision)
            alert_sent = True
        except DeliveryError as e:
            logger.warning(f"Webhook-triggered anomaly alert failed: {e}")

    output_path = f"data/{client_name.lower().replace(' ', '_')}_webhook_report.pdf"
    trend_path = f"{os.path.splitext(output_path)[0]}_trend.png"
    channel_path = f"{os.path.splitext(output_path)[0]}_channels.png"
    weekly_trend_chart(df, trend_path)
    channel_breakdown_chart(comparison.current_by_channel, channel_path)

    if offline:
        narrative = generate_narrative_offline(comparison, client_name)
    else:
        try:
            narrative = generate_narrative(comparison, client_name)
        except AnalyzerError as e:
            logger.warning(f"Narrative generation failed ({e}), using offline fallback.")
            narrative = generate_narrative_offline(comparison, client_name)

    build_report(
        output_path=output_path, client_name=client_name, comparison=comparison,
        narrative=narrative, trend_chart_path=trend_path, channel_chart_path=channel_path,
    )
    EfficiencyLedger().log_run(client=client_name, automation="webhook_trigger")

    return jsonify({
        "status": "ok",
        "report_path": output_path,
        "anomaly_severity": decision.overall_severity.value,
        "anomaly_alert_sent": alert_sent,
        "findings_count": len(decision.findings),
    }), 200


if __name__ == "__main__":
    setup_logging()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
