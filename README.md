# Report Autopilot

A small automation platform built around pieces of the AI Automation
Engineer JD that don't get addressed just by shipping one script:

1. **A working automation** (client report generation) — proves you can ship, not just design
2. **A decision-making agent**, not just a narrator — evaluates every report's data against rules and decides whether something needs a same-day alert, independent of the weekly cycle
3. **A self-feeding loop** — a recurring alert-worthy finding automatically becomes a ranked intake opportunity, deduplicated so it doesn't flood the queue. The system nominates its own next work item instead of waiting for a human to notice the pattern.
4. **A webhook trigger** — the JD names "workflow tools (Zapier, Make, custom scripts)" explicitly; this is a real Flask endpoint either of those tools can call
5. **Multi-LLM support, including two genuinely free providers** — the JD names Claude, ChatGPT, and Gemini explicitly; Claude and Gemini are both real, tested integrations, plus Groq (fast open-model inference) as a third real free option. OpenAI is the one left unimplemented, specifically because it's the one *without* a free tier.
6. **An intake & prioritization system** — *"Build the intake system... so the team can surface and rank opportunities on an ongoing basis."*
7. **A leverage dashboard** — *"Report leverage quarterly... to leadership."* Aggregates real logged runs, not a projection.

**→ [BUSINESS_CASE.md](BUSINESS_CASE.md)** — the RPE/efficiency-value math, in the JD's own vocabulary, with every number labeled by proof tier (observed/estimated/benchmarked/assumed).
**→ [VIDEO_SCRIPT.md](VIDEO_SCRIPT.md)** — walkthrough script used for the demo video.

## The agent: deciding, not just narrating

Most of this pipeline reports what happened. `anomaly_agent.py` decides
what should happen next — it evaluates every report's metrics against
rule-based thresholds (ROAS collapse, revenue collapse, spend spike
without matching revenue) and classifies severity (OK / WATCH / ALERT /
CRITICAL). A CRITICAL finding triggers an immediate Slack alert,
separate from and faster than the weekly report cycle — the difference
between an agency finding out a client's campaign broke on Monday's
report versus the same day it happened.

This is rule-based on purpose, not a second LLM call — a decision that
gates a real alert needs to be deterministic and auditable: the same
input must always produce the same decision, and a human must be able
to see exactly which rule fired. Try it:
```bash
python -m pytest tests/test_anomaly_agent.py -v
```
8 tests construct real scenarios (a ROAS collapse, a spend spike with
flat revenue, multiple simultaneous findings) and verify the agent
classifies each correctly — not just that it runs without crashing.

## The self-feeding loop

Every ALERT/CRITICAL finding calls `IntakeStore.add_from_finding()` —
the anomaly agent nominates its own recurring problems as automation
opportunities, ranked by the same $ impact formula as anything a human
would log manually. Deduplicated (`tests/test_intake.py` has explicit
tests proving a finding that fires every week produces ONE queue entry,
not five) and re-opens if the same issue recurs after being marked
shipped. This is "automate as much as possible" applied to opportunity
discovery itself, not just execution.

## Multi-LLM support — including two genuinely free providers

```bash
python -m report_autopilot.cli --data ... --client "Acme Corp" --llm-provider claude   # real, default, paid
python -m report_autopilot.cli --data ... --client "Acme Corp" --llm-provider gemini   # real, FREE tier
python -m report_autopilot.cli --data ... --client "Acme Corp" --llm-provider groq     # real, FREE tier
python -m report_autopilot.cli --data ... --client "Acme Corp" --llm-provider openai   # not implemented, no free tier
```
`llm_providers.py` is the swappable interface — `cli.py` and
`webhook_server.py` depend only on it, never on a specific vendor's SDK
directly.

- **Claude** — real, delegates to the same tested retry logic
  `analyzer.py` already has. Paid API.
- **Gemini** — real integration against Google's current `google-genai`
  SDK, with retry/fail-fast classification tested against the actual
  SDK exception types. Free API key, no credit card required as of
  writing: https://aistudio.google.com/apikey
- **Groq** — real integration (OpenAI-compatible client) serving fast
  inference on open-weight models like Llama 3.3. Free API key, no
  credit card required as of writing: https://console.groq.com/keys
- **OpenAI** — deliberately not implemented. Unlike Gemini/Groq, OpenAI
  doesn't currently offer a meaningful ongoing free tier, so effort went
  to the two providers that actually meet "free" rather than building
  out a third paid option.

Any provider failure (missing key, rate limit, network issue, or an
unimplemented provider) automatically falls back to the offline
narrative — proven with real CLI runs, not just unit tests:
```bash
python -m report_autopilot.cli --data sample_data/sample_campaign_data.csv --client "Acme Corp" --llm-provider gemini
# without GEMINI_API_KEY set -> logs the exact free-signup link, falls back, report still ships
```

## The webhook trigger (Zapier / Make / custom scripts)

```bash
python -m report_autopilot.webhook_server
```
Exposes `POST /generate-report` — the exact same tested pipeline as the
CLI, callable from Zapier's "Webhooks" action, a Make.com scenario, or
any HTTP client. Protected by a shared-secret header
(`WEBHOOK_SECRET` env var) — proportionate auth for an internal trigger
endpoint, not a public API; the server logs loudly if that's left unset
rather than silently running open.
```bash
curl -X POST http://localhost:5000/generate-report \
  -H "Content-Type: application/json" \
  -d '{"client_name": "Acme Corp", "data_path": "sample_data/sample_campaign_data.csv", "offline": true}'
```

## Try it visually (no terminal needed)

```bash
pip install -r requirements.txt
streamlit run app.py
```
Three tabs: **Generate Report** (upload a CSV or use the sample data,
get a branded PDF), **Automation Intake** (log a workflow pain point,
see it ranked by estimated annual $ impact against everything else
logged), **Leverage Dashboard** (aggregated hours/$ saved from every
real run, broken down by client). Every tab is a visual front-end over
the exact same tested pipeline the CLI uses — zero new business logic.

## What it does

Given a CSV export (Google Ads, Meta Ads, GA4, or a generic format) and
a client, it:

1. Normalizes the export into one common schema, regardless of platform
2. Computes real period-over-period metrics (spend, revenue, ROAS, CPA,
   conversions) — overall and broken down by channel
3. Sends the **computed numbers** (never raw data) to Claude, which
   writes the plain-English narrative a client actually reads — with
   automatic retries on transient failures and a safe offline fallback
   if the API is unavailable
4. Generates two charts and assembles everything into a branded PDF
5. Optionally emails the report and/or posts a Slack notification

**Time impact (estimated):** a manual weekly report commonly takes an
account manager 1.5–3 hours per client. This reduces that to a 5–10
minute review of the generated draft. *(Estimate based on commonly
cited agency reporting-time figures, not observed data from a specific
team — worth validating against real time-tracking before quoting it
as fact to stakeholders.)*

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env and add your real ANTHROPIC_API_KEY (and SMTP_* if using email delivery)
export $(cat .env | xargs)
```

## Usage

**Quick / single-client:**
```bash
python -m report_autopilot.cli \
  --data sample_data/sample_campaign_data.csv \
  --client "Acme Corp" \
  --output data/acme_weekly_report.pdf
```

**Test without an API key** (uses a templated, non-AI narrative):
```bash
python -m report_autopilot.cli --data sample_data/sample_campaign_data.csv --client "Acme Corp" --offline
```

**Production path — branded, with automatic delivery:**
```bash
python -m report_autopilot.cli \
  --data data_exports/acme_latest.csv \
  --client-config sample_data/client_configs/acme_corp.json \
  --deliver
```
`--client-config` points at a small JSON file with the client's brand
color, agency name, and delivery settings (email recipients / Slack
webhook) — see `sample_data/client_configs/acme_corp.json`. `--deliver`
sends the finished PDF out automatically instead of leaving it for
someone to find and send manually.

Full options: `python -m report_autopilot.cli --help`

**View aggregated leverage** (the "report to leadership" piece — reflects real logged runs):
```bash
python -m report_autopilot.leverage_summary
python -m report_autopilot.leverage_summary --hourly-cost 55   # adjust the $/hr assumption
python -m report_autopilot.leverage_summary --json              # machine-readable
```

## Running the tests

```bash
python -m pytest tests/ -v
```
102 tests covering: data loading and platform mapping, metrics
correctness (including zero-division edge cases), the offline/online
narrative paths (including mocked auth errors, connection errors, and
retry-then-recover), the multi-LLM provider abstraction (Claude
delegation, Gemini and Groq's real retry/fail-fast classification
tested against the actual SDK exception types, OpenAI's honest
not-implemented gap, fallback-never-crashes for all of them), client
config loading, delivery success/failure/partial-failure paths, the
anomaly agent's decision logic across real scenarios, the self-feeding
intake loop's deduplication behavior, the webhook server's routes and
auth (via Flask's test client, hitting real request/response cycles),
and full CLI integration (including two genuinely anomalous-data
regression tests proving the self-feeding loop writes to isolated
storage, never production, through both the CLI and webhook paths).

## Automating it

See `scheduler_example.py` for cron and multi-client batch examples.
Point `CLIENTS` at each client's latest export and it runs unattended.

**Runbook if something goes wrong:**
- **Missing/renamed columns in a new export** → add a mapping in
  `data_loader.py`'s `COLUMN_MAPS`; the error names exactly which
  columns were expected vs. found.
- **Claude API fails** → retries transient errors (connection/timeout/
  rate-limit) up to 3x with exponential backoff, fails fast on
  permanent errors (auth/bad request), and falls back to a templated
  narrative either way so the report still ships. Check
  `logs/report_autopilot.log` for the specific error.
- **Delivery fails** → logged as a warning per-channel, does not stop
  the run — the report is already saved locally by the time delivery
  runs, so a failed email/Slack push is recoverable, not a lost report.
- **Numbers look wrong** → check `metrics.py` first. Every number in
  the report traces back to one function there.

## Design decisions worth knowing

- **The LLM never computes numbers, only writes prose around numbers
  Python already computed** — avoids the failure mode where an LLM
  quietly gets arithmetic wrong in a client-facing document.
- **Retries only happen on genuinely transient errors.** An auth
  failure or bad request will never succeed by retrying, so those fail
  immediately instead of wasting three attempts and 6+ seconds before
  falling back.
- **Delivery failures never take down report generation.** The PDF
  exists on disk before delivery is attempted; losing a Slack ping is
  recoverable, silently losing the report would not be.
- **Every export platform needs an explicit column mapping**, not a
  best-effort guess — a silently wrong mapping (e.g. reading "Amount
  spent" as revenue instead of cost) is a worse failure than an
  explicit error telling you to add the mapping.

## What's still not built (honest scope)

- **No live API pull yet.** `report_autopilot/connectors/` has a
  documented interface (`base.py`) and one honestly-labeled stub
  (`google_ads.py`) showing exactly what real OAuth integration would
  require — it raises `NotImplementedError` on purpose rather than
  faking data. Realistic estimate: 1-2 days per platform once API
  access is approved (most of that time is OAuth setup and the
  account-approval wait, not the code). CSV export is the working path
  today.
- **Slack delivery sends a text notification, not the file itself** —
  Slack webhooks can't attach files; pair it with a shared drive link
  in practice.
- **Single comparison window** (this period vs. last) — no month-over-
  month or year-over-year view yet.
- **No secrets manager integration** — currently reads credentials
  from environment variables / `.env`, which is fine for one person
  running this locally but not for a team; a real deployment should
  pull from something like AWS Secrets Manager or Vault instead.

## Project structure

```
report_autopilot/
  data_loader.py       # CSV loading + platform-specific column mapping
  metrics.py             # period-over-period KPI computation
  anomaly_agent.py         # rule-based decision agent (not just narration)
  analyzer.py                # Claude API call w/ retries + offline fallback
  llm_providers.py             # multi-LLM: Claude + Gemini + Groq (2 free), OpenAI honest gap
  charts.py                      # matplotlib chart generation
  report_builder.py                # reportlab PDF assembly
  client_config.py                   # per-client branding + delivery settings
  delivery.py                          # email (SMTP) + Slack (report + anomaly alert)
  intake.py                              # automation intake + ROI-ranked prioritization + self-feeding loop
  efficiency_ledger.py                     # logs every run, aggregates leverage generated
  leverage_summary.py                        # CLI to print the leadership-facing summary
  logging_config.py                            # rotating file + console logging setup
  cli.py                                         # command-line entry point (auto-logs + agent + intake)
  webhook_server.py                                # Flask trigger endpoint (Zapier/Make/curl)
  connectors/
    base.py                                          # interface for live API connectors
    google_ads.py                                       # documented stub (not yet implemented)
app.py                                                    # 3-tab Streamlit demo (report / intake / leverage)
tests/                                                       # 102 tests across every module above
sample_data/
  sample_campaign_data.csv                                    # realistic sample data
  client_configs/acme_corp.json                                 # example client config
scheduler_example.py                                              # cron / batch scheduling examples
requirements.txt
.env.example
BUSINESS_CASE.md    # RPE math in the JD's vocabulary, proof-tier labeled
VIDEO_SCRIPT.md      # walkthrough script for the demo video
```

## What each piece maps to in the JD (explicit, not left as an exercise)

| JD line | What covers it |
|---|---|
| "Ship production AI systems... integrating LLMs, APIs" | `analyzer.py`, `llm_providers.py`, `cli.py`, `delivery.py` |
| "LLMs (Claude, ChatGPT, Gemini)" | `llm_providers.py` — Claude + Gemini + Groq are real integrations (two of them free), OpenAI is the one honest gap |
| "Workflow tools (Zapier, Make, custom scripts)" | `webhook_server.py` — a real HTTP trigger endpoint |
| "You think in leverage, not tasks" | `anomaly_agent.py` — decides what needs attention, doesn't just report it |
| "You'd rather have a rough number than no number" | Every estimate is explicitly labeled estimated vs. observed, everywhere |
| "Document everything... SOP and Loom walkthrough" | This README + `VIDEO_SCRIPT.md` |
| "You've built something that saved real time... tell us exactly how much" | `BUSINESS_CASE.md`, proof-tier labeled |
| "Build the intake system... surface and rank opportunities" | `intake.py` — ROI-ranked, and self-populating via `add_from_finding()` |
| "Report leverage quarterly... to leadership" | `efficiency_ledger.py` + `leverage_summary.py` — aggregates real logged runs |

