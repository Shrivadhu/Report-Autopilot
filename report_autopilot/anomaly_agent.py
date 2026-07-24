"""
anomaly_agent.py
-----------------
Everything else in this pipeline REPORTS what happened. This module
DECIDES what should happen next -- the distinction the JD draws when it
says "you think in leverage, not tasks" and asks for agents, not just
scripts. A weekly PDF is useful; a system that notices a client's ROAS
just fell off a cliff and pages someone same-day, instead of burying it
in next Monday's report, is the difference between reporting and
automation.

This is deliberately rule-based, not a second LLM call, for a specific
reason: severity classification that gates a real alert (paging someone,
possibly urgently) needs to be deterministic and auditable -- the same
input must always produce the same decision, and a human must be able
to see exactly which rule fired and why. An LLM call here would trade
that determinism for fluency it doesn't need; the narrative LLM call in
analyzer.py already covers "explain this in plain English" -- this
module's job is strictly "decide what to do," not "describe."
"""

from dataclasses import dataclass, field
from enum import Enum

from report_autopilot.metrics import PeriodComparison, pct_change


class Severity(Enum):
    OK = "ok"
    WATCH = "watch"          # worth noting in the weekly report, no immediate action
    ALERT = "alert"           # worth a same-day Slack/email ping
    CRITICAL = "critical"      # worth an immediate ping + explicit call-out


@dataclass
class Finding:
    severity: Severity
    rule: str            # which rule fired, for auditability
    channel: str
    message: str
    metric_value: float = None


@dataclass
class AgentDecision:
    overall_severity: Severity
    findings: list = field(default_factory=list)   # list[Finding]
    should_alert_immediately: bool = False

    @property
    def alert_findings(self):
        return [f for f in self.findings if f.severity in (Severity.ALERT, Severity.CRITICAL)]


# Thresholds are named constants, not magic numbers buried in logic --
# a real deployment would tune these per-client (a client with naturally
# volatile spend needs looser thresholds than a stable enterprise
# account), which is why they're parameters to evaluate(), not hardcoded.
DEFAULT_THRESHOLDS = {
    "roas_drop_watch_pct": -15.0,       # ROAS fell 15%+ vs prior period -> WATCH
    "roas_drop_alert_pct": -30.0,        # ROAS fell 30%+ -> ALERT
    "roas_drop_critical_pct": -50.0,      # ROAS fell 50%+ -> CRITICAL, page someone now
    "spend_spike_no_revenue_pct": 40.0,    # spend up 40%+ while revenue flat/down -> ALERT
    "revenue_drop_critical_pct": -40.0,     # total revenue fell 40%+ -> CRITICAL
}


def evaluate(comparison: PeriodComparison, thresholds: dict = None) -> AgentDecision:
    """
    The core decision function. Pure and deterministic: same
    comparison + same thresholds always produces the same decision --
    this is what makes it auditable and safe to gate a real alert on.
    """
    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    findings = []

    # --- Rule 1: overall revenue collapse ---
    rev_change = pct_change(comparison.previous_totals.revenue, comparison.current_totals.revenue)
    if rev_change is not None and rev_change <= thresholds["revenue_drop_critical_pct"]:
        findings.append(Finding(
            severity=Severity.CRITICAL, rule="revenue_drop_critical", channel="TOTAL",
            message=f"Total revenue fell {rev_change:.1f}% vs the prior period -- this needs a same-day look, not next week's report.",
            metric_value=rev_change,
        ))

    # --- Rule 2, 3, 4: per-channel ROAS collapse (worst case wins per channel) ---
    prev_by_channel = {c.channel: c for c in comparison.previous_by_channel}
    for cur in comparison.current_by_channel:
        prev = prev_by_channel.get(cur.channel)
        if prev is None or prev.roas == 0:
            continue  # no prior baseline -- can't evaluate a % change safely
        roas_change = pct_change(prev.roas, cur.roas)
        if roas_change is None:
            continue

        if roas_change <= thresholds["roas_drop_critical_pct"]:
            findings.append(Finding(
                severity=Severity.CRITICAL, rule="roas_drop_critical", channel=cur.channel,
                message=f"{cur.channel} ROAS dropped {roas_change:.1f}% ({prev.roas:.2f}x -> {cur.roas:.2f}x).",
                metric_value=roas_change,
            ))
        elif roas_change <= thresholds["roas_drop_alert_pct"]:
            findings.append(Finding(
                severity=Severity.ALERT, rule="roas_drop_alert", channel=cur.channel,
                message=f"{cur.channel} ROAS dropped {roas_change:.1f}% ({prev.roas:.2f}x -> {cur.roas:.2f}x) -- worth a same-day check.",
                metric_value=roas_change,
            ))
        elif roas_change <= thresholds["roas_drop_watch_pct"]:
            findings.append(Finding(
                severity=Severity.WATCH, rule="roas_drop_watch", channel=cur.channel,
                message=f"{cur.channel} ROAS softened {roas_change:.1f}% -- keep an eye on it.",
                metric_value=roas_change,
            ))

    # --- Rule 5: spend spike without matching revenue growth ---
    for cur in comparison.current_by_channel:
        prev = prev_by_channel.get(cur.channel)
        if prev is None or prev.cost == 0:
            continue
        spend_change = pct_change(prev.cost, cur.cost)
        revenue_change = pct_change(prev.revenue, cur.revenue) if prev.revenue else None
        if spend_change is None:
            continue
        if spend_change >= thresholds["spend_spike_no_revenue_pct"] and (
            revenue_change is None or revenue_change < spend_change / 2
        ):
            findings.append(Finding(
                severity=Severity.ALERT, rule="spend_spike_no_revenue", channel=cur.channel,
                message=(
                    f"{cur.channel} spend rose {spend_change:.1f}% but revenue didn't keep pace "
                    f"({'no prior revenue baseline' if revenue_change is None else f'{revenue_change:.1f}%'}) "
                    f"-- budget may be getting wasted."
                ),
                metric_value=spend_change,
            ))

    if not findings:
        return AgentDecision(overall_severity=Severity.OK, findings=[], should_alert_immediately=False)

    severity_order = [Severity.OK, Severity.WATCH, Severity.ALERT, Severity.CRITICAL]
    overall = max(findings, key=lambda f: severity_order.index(f.severity)).severity
    should_alert = overall in (Severity.ALERT, Severity.CRITICAL)

    return AgentDecision(overall_severity=overall, findings=findings, should_alert_immediately=should_alert)
