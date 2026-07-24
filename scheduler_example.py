"""
scheduler_example.py
---------------------
Shows two realistic ways to run this on a schedule so reports go out
automatically every week without anyone remembering to trigger it.

This intentionally isn't a built-in scheduler daemon -- for a small
number of clients, the standard OS tools below are simpler and more
reliable than a custom always-running Python process (fewer moving
parts to break silently over a holiday weekend).
"""

# ---------------------------------------------------------------------------
# OPTION 1: cron (Linux/Mac) -- add a line like this via `crontab -e`
# ---------------------------------------------------------------------------
#
# Run every Monday at 8:00 AM, generating the report for one client:
#
#   0 8 * * 1 cd /path/to/report-autopilot && \
#     /usr/bin/python3 -m report_autopilot.cli \
#       --data /path/to/latest_export.csv \
#       --client "Acme Corp" \
#       --output /path/to/reports/acme_$(date +\%Y-\%m-\%d).pdf \
#       >> /path/to/logs/report_autopilot.log 2>&1
#
# For multiple clients, add one cron line per client, or loop over a
# config file -- see run_all_clients() below for the loop version.


# ---------------------------------------------------------------------------
# OPTION 2: a simple Python loop + a task scheduler (cron, Windows Task
# Scheduler, or a CI tool like GitHub Actions on a schedule trigger)
# ---------------------------------------------------------------------------

import subprocess
import sys
from datetime import date

# One entry per client: (client_name, path to their latest data export, platform)
CLIENTS = [
    ("Acme Corp", "data_exports/acme_latest.csv", "generic"),
    # ("Another Client", "data_exports/another_latest.csv", "google_ads"),
]


def run_all_clients():
    today = date.today().isoformat()
    failures = []

    for client_name, data_path, platform in CLIENTS:
        output_path = f"reports/{client_name.lower().replace(' ', '_')}_{today}.pdf"
        cmd = [
            sys.executable, "-m", "report_autopilot.cli",
            "--data", data_path,
            "--client", client_name,
            "--platform", platform,
            "--output", output_path,
        ]
        print(f"Generating report for {client_name}...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  FAILED: {result.stderr}", file=sys.stderr)
            failures.append(client_name)
        else:
            print(f"  OK -> {output_path}")

    if failures:
        print(f"\n{len(failures)} client(s) failed: {failures}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run_all_clients())
