"""
cli.py
------
Command-line entry point. This is what a real user (or a cron job)
actually runs.

Basic usage:
    python -m report_autopilot.cli \\
        --data sample_data/sample_campaign_data.csv \\
        --client "Acme Corp" \\
        --output data/acme_weekly_report.pdf

    # No ANTHROPIC_API_KEY set, or want to test without burning API calls:
    python -m report_autopilot.cli --data ... --client ... --offline

Production usage with per-client branding + automatic delivery:
    python -m report_autopilot.cli \\
        --data data_exports/acme_latest.csv \\
        --client-config sample_data/client_configs/acme_corp.json \\
        --deliver
"""

import argparse
import logging
import os
import sys

from report_autopilot.data_loader import load_csv, DataLoadError, COLUMN_MAPS
from report_autopilot.metrics import compare_periods
from report_autopilot.charts import weekly_trend_chart, channel_breakdown_chart
from report_autopilot.analyzer import generate_narrative, generate_narrative_offline, AnalyzerError
from report_autopilot.llm_providers import get_provider
from report_autopilot.report_builder import build_report
from report_autopilot.client_config import load_client_config, default_config, ClientConfigError
from report_autopilot.delivery import deliver_report, send_anomaly_alert, DeliveryError
from report_autopilot.intake import IntakeStore
from report_autopilot.efficiency_ledger import EfficiencyLedger
from report_autopilot.anomaly_agent import evaluate as evaluate_anomalies, Severity
from report_autopilot.logging_config import setup_logging

logger = logging.getLogger("report_autopilot.cli")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate a client performance report PDF.")
    parser.add_argument("--data", required=True, help="Path to the campaign performance CSV export.")
    parser.add_argument("--platform", default="generic", choices=list(COLUMN_MAPS),
                         help="Which export format the CSV is in (default: generic).")

    client_group = parser.add_mutually_exclusive_group(required=True)
    client_group.add_argument("--client", help="Client name, shown on the report (uses default branding).")
    client_group.add_argument("--client-config", help="Path to a client config JSON file (branding + delivery settings).")

    parser.add_argument("--agency", default="Single Grain", help="Agency name shown as report author (ignored if --client-config sets one).")
    parser.add_argument("--period-days", type=int, default=7,
                         help="Length of the comparison period in days (default: 7, i.e. week-over-week).")
    parser.add_argument("--output", default=None, help="Output PDF path (default: data/<client>_report.pdf).")
    parser.add_argument("--offline", action="store_true",
                         help="Skip the Claude API call and use a templated narrative instead. "
                              "Useful for testing, or as a fallback if the API is unavailable.")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Claude model to use for the narrative.")
    parser.add_argument("--llm-provider", default="claude", choices=["claude", "gemini", "groq", "openai"],
                         help="Which LLM provider to use for the narrative (default: claude). "
                              "claude/gemini/groq are real, tested integrations -- gemini and groq "
                              "both have genuinely free API tiers (see llm_providers.py for signup links). "
                              "openai is not implemented (no meaningful free tier). Any failure or "
                              "unimplemented provider falls back to the offline narrative automatically.")
    parser.add_argument("--deliver", action="store_true",
                         help="After generating the report, send it via the delivery channels "
                              "configured in --client-config (email/Slack). Requires --client-config.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    setup_logging(level=getattr(logging, args.log_level))

    if args.deliver and not args.client_config:
        logger.error("--deliver requires --client-config (delivery settings live there).")
        return 1

    if args.client_config:
        try:
            client_config = load_client_config(args.client_config)
        except ClientConfigError as e:
            logger.error(f"Client config error: {e}")
            return 1
    else:
        client_config = default_config(args.client, agency_name=args.agency)

    output_path = args.output or f"data/{client_config.client_name.lower().replace(' ', '_')}_report.pdf"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    try:
        df = load_csv(args.data, platform=args.platform)
    except DataLoadError as e:
        logger.error(f"Error loading data: {e}")
        return 1
    logger.info(f"Loaded {len(df)} rows from {args.data}")

    try:
        comparison = compare_periods(df, period_days=args.period_days)
    except ValueError as e:
        logger.error(f"Error computing metrics: {e}")
        return 1

    # The agent runs on every generation, independent of --deliver --
    # a same-day problem shouldn't wait on whether someone remembered
    # to pass the delivery flag for the routine weekly report.
    decision = evaluate_anomalies(comparison)
    if decision.findings:
        for finding in decision.findings:
            level = logging.CRITICAL if finding.severity == Severity.CRITICAL else logging.WARNING
            logger.log(level, f"[{finding.severity.value.upper()}] {finding.channel}: {finding.message}")

    # Self-feeding loop: an alert-worthy finding IS an automation
    # opportunity -- someone is spending time on it manually whether or
    # not anyone's logged that yet. add_from_finding() dedupes, so a
    # recurring weekly finding produces one queue entry, not a flood.
    for finding in decision.alert_findings:
        intake_store = IntakeStore()
        new_opp = intake_store.add_from_finding(client_config.client_name, finding)
        if new_opp:
            logger.info(f"Auto-logged intake opportunity {new_opp.id}: {new_opp.description}")

    if decision.should_alert_immediately and client_config.delivery.slack_webhook_url:
        try:
            send_anomaly_alert(client_config.delivery.slack_webhook_url, client_config.client_name, decision)
            logger.info("Sent immediate anomaly alert to Slack.")
        except DeliveryError as e:
            logger.warning(f"Anomaly alert Slack delivery failed: {e}")
    elif decision.should_alert_immediately:
        logger.warning(
            f"Anomaly severity is {decision.overall_severity.value.upper()} but no Slack webhook "
            f"is configured for {client_config.client_name} -- alert was logged but not pushed anywhere."
        )

    trend_path = f"{os.path.splitext(output_path)[0]}_trend.png"
    channel_path = f"{os.path.splitext(output_path)[0]}_channels.png"
    weekly_trend_chart(df, trend_path, brand_color=client_config.brand_color)
    channel_breakdown_chart(comparison.current_by_channel, channel_path, brand_color=client_config.brand_color)

    if args.offline:
        narrative = generate_narrative_offline(comparison, client_config.client_name)
    elif args.llm_provider != "claude":
        # gemini/groq are real integrations and can raise AnalyzerError
        # (missing key, rate limit, network issue, etc.); openai is a
        # stub and raises NotImplementedError. Either way: log it, fall
        # back, keep shipping the report -- a provider failure should
        # never be the reason a scheduled report doesn't go out.
        try:
            provider = get_provider(args.llm_provider)
            narrative = provider.generate_narrative(comparison, client_config.client_name)
        except (AnalyzerError, NotImplementedError, ValueError) as e:
            logger.warning(
                f"LLM provider {args.llm_provider!r} failed ({e}). "
                f"Falling back to the offline templated summary so the report still ships on schedule."
            )
            narrative = generate_narrative_offline(comparison, client_config.client_name)
    else:
        try:
            narrative = generate_narrative(comparison, client_config.client_name, model=args.model)
        except AnalyzerError as e:
            logger.warning(
                f"Claude narrative generation failed ({e}). "
                f"Falling back to the offline templated summary so the report still ships on schedule."
            )
            narrative = generate_narrative_offline(comparison, client_config.client_name)

    build_report(
        output_path=output_path,
        client_name=client_config.client_name,
        comparison=comparison,
        narrative=narrative,
        trend_chart_path=trend_path,
        channel_chart_path=channel_path,
        agency_name=client_config.agency_name,
        brand_color=client_config.brand_color,
    )
    logger.info(f"Report generated: {output_path}")

    ledger = EfficiencyLedger()
    ledger.log_run(client=client_config.client_name)

    if args.deliver:
        results = deliver_report(client_config, output_path, comparison=comparison)
        for channel, outcome in results.items():
            level = logging.INFO if outcome == "sent" else logging.WARNING
            logger.log(level, f"Delivery [{channel}]: {outcome}")

    print(f"Report generated: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
