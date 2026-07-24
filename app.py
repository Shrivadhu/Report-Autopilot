"""
app.py
------
A visual front-end over three separate pieces built for this role,
shown as one platform:

1. Generate Report -- the core automation (client reporting)
2. Automation Intake -- the "intake and prioritization framework" the
   JD names as its own responsibility, separate from any one automation
3. Leverage Dashboard -- the "report leverage quarterly" responsibility,
   aggregating real logged runs, not a projection

Every function called here is the same tested code used by the CLI
(report_autopilot/*.py) -- this file adds zero new business logic,
only visibility into it.

Run with:
    streamlit run app.py
"""

import os
import tempfile

import streamlit as st

from report_autopilot.data_loader import load_csv, DataLoadError, COLUMN_MAPS
from report_autopilot.metrics import compare_periods
from report_autopilot.charts import weekly_trend_chart, channel_breakdown_chart
from report_autopilot.analyzer import generate_narrative, generate_narrative_offline, AnalyzerError
from report_autopilot.report_builder import build_report
from report_autopilot.intake import IntakeStore
from report_autopilot.efficiency_ledger import EfficiencyLedger

st.set_page_config(page_title="Report Autopilot", page_icon="📊", layout="centered")
st.title("📊 Report Autopilot")
st.caption("A small automation platform: ship an automation, log requests for the next one, track the leverage created.")

tab_generate, tab_intake, tab_leverage = st.tabs(["Generate Report", "Automation Intake", "Leverage Dashboard"])

# =============================================================================
# TAB 1: Generate Report
# =============================================================================
with tab_generate:
    with st.sidebar:
        st.header("Report Settings")
        client_name = st.text_input("Client name", value="Acme Corp")
        agency_name = st.text_input("Agency name", value="Single Grain")
        brand_color = st.color_picker("Brand color", value="#f27038")
        platform = st.selectbox("Export platform", options=list(COLUMN_MAPS.keys()), index=0)
        period_days = st.slider("Comparison period (days)", min_value=3, max_value=30, value=7)
        use_ai = st.checkbox(
            "Use Claude for the narrative (unchecked = fast templated summary, no API call)",
            value=bool(os.environ.get("ANTHROPIC_API_KEY")),
        )
        if use_ai and not os.environ.get("ANTHROPIC_API_KEY"):
            st.warning("ANTHROPIC_API_KEY is not set -- will fall back to the templated summary automatically.")

    uploaded_file = st.file_uploader("Campaign performance CSV", type=["csv"])
    use_sample = st.checkbox("...or just use the sample dataset (no upload needed)", value=uploaded_file is None)

    if st.button("Generate Report", type="primary", disabled=not (uploaded_file or use_sample)):
        with st.spinner("Loading and validating data..."):
            try:
                if use_sample:
                    data_path = "sample_data/sample_campaign_data.csv"
                else:
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
                    tmp.write(uploaded_file.getvalue())
                    tmp.close()
                    data_path = tmp.name
                df = load_csv(data_path, platform=platform)
            except DataLoadError as e:
                st.error(f"Couldn't load this file: {e}")
                st.stop()

        st.success(f"Loaded {len(df)} rows.")

        with st.spinner("Computing metrics..."):
            comparison = compare_periods(df, period_days=period_days)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Revenue", f"{comparison.current_totals.revenue:,.0f}")
        col2.metric("Spend", f"{comparison.current_totals.cost:,.0f}")
        col3.metric("ROAS", f"{comparison.current_totals.roas:.2f}x")
        col4.metric("Conversions", f"{comparison.current_totals.conversions:,.0f}")

        with st.spinner("Building charts..."):
            trend_path = tempfile.mktemp(suffix="_trend.png")
            channel_path = tempfile.mktemp(suffix="_channels.png")
            weekly_trend_chart(df, trend_path, brand_color=brand_color)
            channel_breakdown_chart(comparison.current_by_channel, channel_path, brand_color=brand_color)

        st.image(trend_path, caption="Weekly Revenue Trend")
        st.image(channel_path, caption="Revenue by Channel")

        with st.spinner("Writing narrative..." + (" (calling Claude)" if use_ai else "")):
            if use_ai:
                try:
                    narrative = generate_narrative(comparison, client_name)
                    st.info("Narrative written by Claude.")
                except AnalyzerError as e:
                    st.warning(f"Claude call failed ({e}) -- used the templated fallback instead.")
                    narrative = generate_narrative_offline(comparison, client_name)
            else:
                narrative = generate_narrative_offline(comparison, client_name)

        st.subheader("Report Narrative")
        st.write(narrative)

        with st.spinner("Assembling PDF..."):
            pdf_path = tempfile.mktemp(suffix=".pdf")
            build_report(
                output_path=pdf_path, client_name=client_name, comparison=comparison,
                narrative=narrative, trend_chart_path=trend_path, channel_chart_path=channel_path,
                agency_name=agency_name, brand_color=brand_color,
            )

        # Log this run to the leverage ledger -- same call the CLI makes,
        # so the Leverage Dashboard tab reflects real usage from this tab too.
        EfficiencyLedger().log_run(client=client_name)

        st.success("Report ready. This run has been logged to the Leverage Dashboard tab.")
        with open(pdf_path, "rb") as f:
            st.download_button(
                "Download PDF", data=f.read(),
                file_name=f"{client_name.lower().replace(' ', '_')}_report.pdf",
                mime="application/pdf",
            )

# =============================================================================
# TAB 2: Automation Intake
# =============================================================================
with tab_intake:
    st.caption(
        "The JD names this as its own responsibility, separate from shipping any "
        "one automation: \"Build the intake system... so the team can surface and "
        "rank opportunities on an ongoing basis.\" This is that system."
    )

    intake_store = IntakeStore()

    with st.form("intake_form"):
        st.subheader("Log a new automation opportunity")
        col1, col2 = st.columns(2)
        department = col1.text_input("Department", placeholder="e.g. Content, Ops, Sales")
        submitted_by = col2.text_input("Submitted by", placeholder="e.g. your name")
        description = st.text_area("What's the manual/repetitive workflow?", placeholder="e.g. Manually formatting client reports every week")
        col3, col4, col5 = st.columns(3)
        hours_per_week = col3.number_input("Hours/week on this", min_value=0.0, value=2.0, step=0.5)
        people_affected = col4.number_input("People doing this", min_value=1, value=1, step=1)
        hourly_cost = col5.number_input("Hourly cost ($)", min_value=0.0, value=45.0, step=5.0)

        if st.form_submit_button("Add to intake queue"):
            if department and description:
                opp = intake_store.add(department, description, hours_per_week, people_affected, hourly_cost, submitted_by)
                st.success(f"Logged as {opp.id} — estimated annual impact: ${opp.annual_impact_usd:,.0f}")
            else:
                st.error("Department and description are required.")

    st.subheader("Ranked opportunities (highest impact first)")
    ranked = intake_store.ranked_by_impact()
    if not ranked:
        st.info("No opportunities logged yet — add one above.")
    else:
        st.dataframe(
            [{
                "ID": o.id, "Department": o.department, "Description": o.description,
                "Status": o.status, "Annual Hours": f"{o.annual_hours_saved:,.0f}",
                "Annual $ Impact": f"${o.annual_impact_usd:,.0f}",
            } for o in ranked],
            use_container_width=True, hide_index=True,
        )

# =============================================================================
# TAB 3: Leverage Dashboard
# =============================================================================
with tab_leverage:
    st.caption(
        "The JD: \"Report leverage quarterly. Track and present recurring "
        "efficiency value... to leadership.\" This aggregates every real run "
        "logged from the Generate Report tab and the CLI — not a projection."
    )

    hourly_cost_input = st.number_input("Fully-loaded hourly cost for $ conversion", min_value=0.0, value=45.0, step=5.0)
    ledger = EfficiencyLedger()
    summary = ledger.summarize(hourly_cost_usd=hourly_cost_input)

    col1, col2, col3 = st.columns(3)
    col1.metric("Automation runs logged", summary["total_runs"])
    col2.metric("Hours saved (est.)", summary["total_hours_saved"])
    col3.metric("Value generated (est.)", f"${summary['total_estimated_value_usd']:,.0f}")

    if summary["total_runs"] == 0:
        st.info("No runs logged yet — generate a report in the first tab to see this populate.")
    else:
        st.subheader("By client")
        st.bar_chart(summary["hours_saved_by_client"])

    st.caption(
        "Note: hours-saved-per-run is a documented estimate from BUSINESS_CASE.md, "
        "not a timed measurement. Run count and the client/automation breakdown "
        "are observed from actual usage of this tool."
    )

st.divider()
st.caption(
    "Every calculation across all three tabs runs through the same tested "
    "pipeline as the CLI (`report_autopilot/`) — this UI adds zero new "
    "business logic, it's a visual front-end over the exact same code."
)
