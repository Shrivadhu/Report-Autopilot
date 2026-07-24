"""
intake.py
---------
Addresses a specific, named responsibility in the JD that a single
report-generation tool doesn't touch on its own: "Build the intake
system. Establish an automation request and prioritization framework
so the team can surface and rank opportunities on an ongoing basis."

This is deliberately simple -- not because the problem is simple, but
because an intake system's value is in being used consistently, not in
being sophisticated. A framework nobody fills out is worthless. JSON +
a ranking formula anyone can inspect beats a complex system that's
opaque about how it ranks things.

Ranking formula (documented, not hidden):
    annual_impact = hours_per_week * 52 * people_affected * hourly_cost_usd

This intentionally uses only inputs a stakeholder can estimate in a
5-minute conversation -- the JD's own "you'd rather have a rough number
than no number" principle, applied to the intake tool itself.
"""

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone

DEFAULT_STORE_PATH = "data/intake_opportunities.json"


@dataclass
class Opportunity:
    id: str
    department: str
    description: str
    hours_per_week: float
    people_affected: int
    hourly_cost_usd: float
    submitted_by: str = ""
    status: str = "proposed"   # proposed | in_progress | shipped | rejected
    submitted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def annual_impact_usd(self) -> float:
        return self.hours_per_week * 52 * self.people_affected * self.hourly_cost_usd

    @property
    def annual_hours_saved(self) -> float:
        return self.hours_per_week * 52 * self.people_affected


class IntakeStore:
    """Simple JSON-backed store. Swappable for a real database later
    without changing the ranking logic below -- the formula only
    depends on the Opportunity dataclass, not the storage mechanism."""

    def __init__(self, path: str = None):
        # Same isolation fix applied to efficiency_ledger.py: default
        # path is overridable via env var so tests (or the webhook
        # server, or multiple environments) never silently write into
        # the same file as production usage.
        self.path = path or os.environ.get("REPORT_AUTOPILOT_INTAKE_PATH", DEFAULT_STORE_PATH)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        if not os.path.exists(self.path):
            with open(self.path, "w") as f:
                json.dump([], f)

    def _read_all(self) -> list:
        with open(self.path) as f:
            raw = json.load(f)
        return [Opportunity(**r) for r in raw]

    def _write_all(self, opportunities: list):
        with open(self.path, "w") as f:
            json.dump([asdict(o) for o in opportunities], f, indent=2)

    def add(self, department, description, hours_per_week, people_affected,
             hourly_cost_usd, submitted_by="") -> Opportunity:
        opportunities = self._read_all()
        new_id = f"OPP-{len(opportunities) + 1:03d}"
        opp = Opportunity(
            id=new_id, department=department, description=description,
            hours_per_week=hours_per_week, people_affected=people_affected,
            hourly_cost_usd=hourly_cost_usd, submitted_by=submitted_by,
        )
        opportunities.append(opp)
        self._write_all(opportunities)
        return opp

    def list_all(self) -> list:
        return self._read_all()

    def ranked_by_impact(self, status_filter: str = None) -> list:
        opportunities = self._read_all()
        if status_filter:
            opportunities = [o for o in opportunities if o.status == status_filter]
        return sorted(opportunities, key=lambda o: o.annual_impact_usd, reverse=True)

    def update_status(self, opportunity_id: str, new_status: str) -> Opportunity:
        valid_statuses = {"proposed", "in_progress", "shipped", "rejected"}
        if new_status not in valid_statuses:
            raise ValueError(f"status must be one of {valid_statuses}, got {new_status!r}")

        opportunities = self._read_all()
        for o in opportunities:
            if o.id == opportunity_id:
                o.status = new_status
                self._write_all(opportunities)
                return o
        raise KeyError(f"No opportunity with id {opportunity_id!r}")

    def total_shipped_impact_usd(self) -> float:
        return sum(o.annual_impact_usd for o in self._read_all() if o.status == "shipped")

    def add_from_finding(self, client_name: str, finding, hours_per_week: float = 1.0,
                          hourly_cost_usd: float = 45.0) -> "Opportunity | None":
        """
        Closes the loop between the anomaly agent and the intake system:
        a recurring problem the agent keeps flagging IS an automation
        opportunity -- someone is spending real time investigating or
        firefighting it manually. This is 'automate as much as possible'
        applied to the intake process itself: the system nominates its
        own next work item instead of waiting for a human to notice the
        pattern and type it in.

        Deduplicates against existing proposed/in_progress opportunities
        with the same department+description, so a CRITICAL finding
        that fires every week doesn't flood the queue with duplicates --
        it should be one entry, prioritized once, not re-logged
        every run.

        Returns the new Opportunity, or None if a matching one already
        exists (no duplicate created).
        """
        department = f"{client_name} — Client Ops"
        description = f"Recurring issue: {finding.rule} on {finding.channel} ({client_name})"

        existing = [
            o for o in self._read_all()
            if o.department == department and o.description == description
            and o.status in ("proposed", "in_progress")
        ]
        if existing:
            return None

        return self.add(
            department=department, description=description,
            hours_per_week=hours_per_week, people_affected=1,
            hourly_cost_usd=hourly_cost_usd, submitted_by="anomaly_agent (auto-generated)",
        )
