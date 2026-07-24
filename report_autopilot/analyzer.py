"""
analyzer.py
-----------
The one LLM call in the whole pipeline: takes the ALREADY-COMPUTED
metrics from metrics.py (never raw numbers the model would have to
do arithmetic on) and asks Claude to write the client-facing summary
-- what happened, why it plausibly happened, and what to watch next.

Deliberate design choice: the model only writes prose around numbers
we computed ourselves in Python. It never invents the numbers or does
the arithmetic. This matters a lot for a real client-facing tool --
LLM arithmetic mistakes are exactly the kind of error that erodes
trust in an agency-client relationship, so the split is: Python is
the source of truth for every number, Claude is the source of the
narrative and interpretation layer only.
"""

import os
import time
import logging
from report_autopilot.metrics import PeriodComparison, pct_change

logger = logging.getLogger("report_autopilot.analyzer")

SYSTEM_PROMPT = """You are a senior marketing analyst writing the narrative section of a \
weekly client performance report for a digital marketing agency.

Rules:
- You will be given already-computed metrics. Never invent or recompute numbers -- \
only use the figures given to you, and you may reference simple relationships between \
them (e.g. "revenue grew while spend stayed flat").
- Write for a client who is not a marketer: no jargon without a one-line explanation.
- Be honest about downturns. Do not spin bad news as good news. If something declined, \
say so plainly and suggest one plausible, clearly-labeled-as-a-hypothesis reason -- do \
not present a guess as a confirmed cause.
- Keep it to 3 short paragraphs: (1) headline summary, (2) what's working, (3) what \
needs attention / a concrete next step.
- Do not use exclamation marks or hype language ("amazing", "incredible"). Sound like a \
careful analyst, not a salesperson.
"""


class AnalyzerError(Exception):
    pass


def _format_metrics_for_prompt(comparison: PeriodComparison) -> str:
    cur, prev = comparison.current_totals, comparison.previous_totals
    lines = [
        f"Current period: {comparison.current_start.date()} to {comparison.current_end.date()}",
        f"Previous period: {comparison.previous_start.date()} to {comparison.previous_end.date()}",
        "",
        "TOTALS (current vs previous):",
        f"- Spend: {cur.cost:,.2f} vs {prev.cost:,.2f} "
        f"({pct_change(prev.cost, cur.cost):+.1f}%)" if pct_change(prev.cost, cur.cost) is not None else "",
        f"- Revenue: {cur.revenue:,.2f} vs {prev.revenue:,.2f} "
        f"({pct_change(prev.revenue, cur.revenue):+.1f}%)" if pct_change(prev.revenue, cur.revenue) is not None else "",
        f"- Conversions: {cur.conversions:,.0f} vs {prev.conversions:,.0f} "
        f"({pct_change(prev.conversions, cur.conversions):+.1f}%)" if pct_change(prev.conversions, cur.conversions) is not None else "",
        f"- Overall ROAS: {cur.roas:.2f}x vs {prev.roas:.2f}x",
        "",
        "BY CHANNEL (current period):",
    ]
    for c in comparison.current_by_channel:
        lines.append(
            f"- {c.channel}: revenue={c.revenue:,.2f}, spend={c.cost:,.2f}, "
            f"ROAS={c.roas:.2f}x, CPA={c.cpa:,.2f}, conversions={c.conversions:,.0f}"
        )

    if comparison.biggest_gainer:
        g = comparison.biggest_gainer
        lines.append(f"\nBiggest revenue gainer vs previous period: {g['channel']} ({g['pct_change']:+.1f}%)")
    if comparison.biggest_decliner:
        d = comparison.biggest_decliner
        lines.append(f"Biggest revenue decliner vs previous period: {d['channel']} ({d['pct_change']:+.1f}%)")

    return "\n".join(l for l in lines if l is not None)


def generate_narrative(comparison: PeriodComparison, client_name: str, model: str = "claude-sonnet-4-6") -> str:
    """
    Calls the Claude API to generate the written narrative section of
    the report. Requires ANTHROPIC_API_KEY to be set in the environment.
    """
    try:
        import anthropic
    except ImportError:
        raise AnalyzerError("The 'anthropic' package is not installed. Run: pip install anthropic")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AnalyzerError(
            "ANTHROPIC_API_KEY is not set. Set it in your environment or .env file "
            "before generating a report. See .env.example."
        )

    client = anthropic.Anthropic(api_key=api_key)
    metrics_block = _format_metrics_for_prompt(comparison)

    # Retry only on TRANSIENT failures (network blips, timeouts, rate
    # limits) -- an auth error or a bad request will never succeed on
    # retry, so those fail fast instead of wasting time before falling
    # back to the offline narrative.
    max_attempts = 3
    base_delay_seconds = 2

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            message = client.messages.create(
                model=model,
                max_tokens=700,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Client name: {client_name}\n\n"
                        f"Here are this week's computed metrics:\n\n{metrics_block}\n\n"
                        f"Write the narrative section of this client's weekly report."
                    ),
                }],
            )
            break  # success

        except anthropic.RateLimitError as e:
            last_error = e
            if attempt < max_attempts:
                delay = base_delay_seconds * (2 ** (attempt - 1))
                logger.warning(f"Rate limited (attempt {attempt}/{max_attempts}), retrying in {delay}s")
                time.sleep(delay)
                continue
            raise AnalyzerError(f"Claude API rate limit exceeded after {max_attempts} attempts: {e}")

        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            last_error = e
            if attempt < max_attempts:
                delay = base_delay_seconds * (2 ** (attempt - 1))
                logger.warning(f"Transient API error (attempt {attempt}/{max_attempts}): {e}. Retrying in {delay}s")
                time.sleep(delay)
                continue
            raise AnalyzerError(f"Claude API unreachable after {max_attempts} attempts: {e}")

        except anthropic.APIStatusError as e:
            # 4xx errors (auth, bad request, etc.) will not succeed on
            # retry -- fail immediately rather than waste time and quota.
            raise AnalyzerError(f"Claude API returned an error (status {e.status_code}): {e.message}")

        except anthropic.APIError as e:
            raise AnalyzerError(f"Claude API call failed: {e}")
    else:
        raise AnalyzerError(f"Claude API call failed after {max_attempts} attempts: {last_error}")

    text_parts = [block.text for block in message.content if block.type == "text"]
    narrative = "\n".join(text_parts).strip()
    if not narrative:
        raise AnalyzerError("Claude returned an empty response.")
    return narrative


def generate_narrative_offline(comparison: PeriodComparison, client_name: str) -> str:
    """
    A deterministic, non-LLM fallback that produces a plain templated
    narrative from the same metrics. Useful for testing the rest of the
    pipeline without an API key, or as a degraded-mode fallback if the
    API call fails and the report still needs to go out on schedule.
    """
    cur, prev = comparison.current_totals, comparison.previous_totals
    rev_change = pct_change(prev.revenue, cur.revenue)
    spend_change = pct_change(prev.cost, cur.cost)

    para1 = (
        f"This week's report for {client_name} covers "
        f"{comparison.current_start.date()} to {comparison.current_end.date()}. "
        f"Revenue was {cur.revenue:,.0f}"
        + (f", a change of {rev_change:+.1f}% versus the prior period" if rev_change is not None else "")
        + f", on spend of {cur.cost:,.0f}"
        + (f" ({spend_change:+.1f}% vs prior period)." if spend_change is not None else ".")
    )

    para2 = "Channel performance this period: " + "; ".join(
        f"{c.channel} generated {c.revenue:,.0f} in revenue at a {c.roas:.2f}x ROAS"
        for c in sorted(comparison.current_by_channel, key=lambda c: c.revenue, reverse=True)
    ) + "."

    para3 = ""
    if comparison.biggest_decliner:
        d = comparison.biggest_decliner
        if d["pct_change"] < 0:
            para3 = (
                f"{d['channel']} saw the largest revenue decline this period "
                f"({d['pct_change']:+.1f}%) and is worth reviewing first."
            )
        else:
            para3 = (
                f"All channels grew revenue this period; {d['channel']} grew the "
                f"slowest ({d['pct_change']:+.1f}%) and is worth keeping an eye on."
            )
    elif comparison.biggest_gainer:
        g = comparison.biggest_gainer
        para3 = f"{g['channel']} was the strongest performer this period ({g['pct_change']:+.1f}%)."

    return "\n\n".join(p for p in [para1, para2, para3] if p)
