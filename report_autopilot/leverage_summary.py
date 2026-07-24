"""
leverage_summary.py
--------------------
Prints the leadership-facing summary the JD asks for: "Report leverage
quarterly. Track and present recurring efficiency value, cycle time
reductions, and RPE impact to leadership." Run this and paste it into
a quarterly update -- every run of report_autopilot.cli logs a ledger
entry automatically, so this reflects real usage, not a projection.

Usage:
    python -m report_autopilot.leverage_summary
    python -m report_autopilot.leverage_summary --hourly-cost 55
"""

import argparse
import json

from report_autopilot.efficiency_ledger import EfficiencyLedger


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print the aggregated efficiency leverage summary.")
    parser.add_argument("--hourly-cost", type=float, default=45.0,
                         help="Fully-loaded hourly cost used to convert hours saved into $ value (default: 45).")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of a formatted report.")
    args = parser.parse_args(argv)

    ledger = EfficiencyLedger()
    summary = ledger.summarize(hourly_cost_usd=args.hourly_cost)

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print("=" * 50)
    print("REPORT AUTOPILOT — LEVERAGE SUMMARY")
    print("=" * 50)
    print(f"Total automation runs logged:     {summary['total_runs']}")
    print(f"Total hours saved (estimated):     {summary['total_hours_saved']}")
    print(f"Estimated value generated:         ${summary['total_estimated_value_usd']:,.2f}")
    print(f"  (at ${args.hourly_cost:.0f}/hr fully-loaded cost)")
    print()
    print("By client:")
    for client, hours in sorted(summary["hours_saved_by_client"].items(), key=lambda x: -x[1]):
        print(f"  {client:<25s} {hours:>6.1f} hrs")
    print()
    print("By automation:")
    for automation, hours in sorted(summary["hours_saved_by_automation"].items(), key=lambda x: -x[1]):
        print(f"  {automation:<25s} {hours:>6.1f} hrs")
    print("=" * 50)
    print("Note: hours-saved-per-run is a documented estimate (see")
    print("BUSINESS_CASE.md), not a timed measurement. Run count and")
    print("client/automation breakdown are observed from actual usage.")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
