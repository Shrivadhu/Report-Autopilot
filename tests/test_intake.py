import json
import pytest

from report_autopilot.intake import IntakeStore, Opportunity


@pytest.fixture
def store(tmp_path):
    return IntakeStore(path=str(tmp_path / "opportunities.json"))


def test_add_creates_opportunity_with_sequential_id(store):
    opp1 = store.add("Marketing", "Manual client reports", 8, 3, 45)
    opp2 = store.add("Ops", "Manual invoice reconciliation", 5, 2, 35)
    assert opp1.id == "OPP-001"
    assert opp2.id == "OPP-002"


def test_annual_impact_calculation_is_correct(store):
    opp = store.add(department="Marketing", description="Manual reports",
                     hours_per_week=8, people_affected=3, hourly_cost_usd=45)
    # 8 hrs/week * 52 weeks * 3 people * $45/hr
    expected = 8 * 52 * 3 * 45
    assert opp.annual_impact_usd == expected
    assert opp.annual_hours_saved == 8 * 52 * 3


def test_ranked_by_impact_sorts_descending(store):
    store.add("Marketing", "Small task", hours_per_week=1, people_affected=1, hourly_cost_usd=30)
    store.add("Ops", "Big task", hours_per_week=10, people_affected=5, hourly_cost_usd=40)
    store.add("Sales", "Medium task", hours_per_week=3, people_affected=2, hourly_cost_usd=35)

    ranked = store.ranked_by_impact()
    assert ranked[0].description == "Big task"
    assert ranked[-1].description == "Small task"
    # confirm actually descending, not just first/last check
    impacts = [o.annual_impact_usd for o in ranked]
    assert impacts == sorted(impacts, reverse=True)


def test_status_defaults_to_proposed(store):
    opp = store.add("Marketing", "Test", 1, 1, 30)
    assert opp.status == "proposed"


def test_update_status_persists(store):
    opp = store.add("Marketing", "Test", 1, 1, 30)
    updated = store.update_status(opp.id, "shipped")
    assert updated.status == "shipped"
    # re-read from disk to confirm it actually persisted, not just in-memory
    reloaded = store.list_all()
    assert reloaded[0].status == "shipped"


def test_update_status_invalid_value_raises(store):
    opp = store.add("Marketing", "Test", 1, 1, 30)
    with pytest.raises(ValueError, match="must be one of"):
        store.update_status(opp.id, "not_a_real_status")


def test_update_status_unknown_id_raises(store):
    with pytest.raises(KeyError):
        store.update_status("OPP-999", "shipped")


def test_ranked_by_impact_filters_by_status(store):
    opp1 = store.add("Marketing", "Task A", 5, 2, 40)
    opp2 = store.add("Ops", "Task B", 3, 1, 35)
    store.update_status(opp1.id, "shipped")

    shipped_only = store.ranked_by_impact(status_filter="shipped")
    assert len(shipped_only) == 1
    assert shipped_only[0].id == opp1.id


def test_total_shipped_impact_only_counts_shipped(store):
    opp1 = store.add("Marketing", "Task A", hours_per_week=5, people_affected=2, hourly_cost_usd=40)
    opp2 = store.add("Ops", "Task B", hours_per_week=3, people_affected=1, hourly_cost_usd=35)
    store.update_status(opp1.id, "shipped")
    # opp2 stays "proposed" -- should NOT count toward total

    total = store.total_shipped_impact_usd()
    assert total == opp1.annual_impact_usd


def test_store_persists_across_instances(tmp_path):
    path = str(tmp_path / "shared.json")
    store1 = IntakeStore(path=path)
    store1.add("Marketing", "Test", 1, 1, 30)

    store2 = IntakeStore(path=path)  # fresh instance, same file
    assert len(store2.list_all()) == 1


# ---------- self-feeding loop: anomaly agent -> intake ----------

class _FakeFinding:
    """Minimal stand-in for anomaly_agent.Finding, avoiding a circular
    import between intake.py's tests and anomaly_agent.py."""
    def __init__(self, rule, channel):
        self.rule = rule
        self.channel = channel


def test_add_from_finding_creates_opportunity(store):
    finding = _FakeFinding(rule="roas_drop_critical", channel="Meta Ads")
    opp = store.add_from_finding("Acme Corp", finding)
    assert opp is not None
    assert "roas_drop_critical" in opp.description
    assert "Meta Ads" in opp.description
    assert opp.submitted_by == "anomaly_agent (auto-generated)"


def test_add_from_finding_deduplicates_identical_repeated_alert(store):
    """The core property this exists for: the same recurring finding
    firing on 5 consecutive weekly runs must produce ONE intake entry,
    not five duplicates flooding the queue."""
    finding = _FakeFinding(rule="roas_drop_critical", channel="Meta Ads")

    first = store.add_from_finding("Acme Corp", finding)
    second = store.add_from_finding("Acme Corp", finding)
    third = store.add_from_finding("Acme Corp", finding)

    assert first is not None
    assert second is None
    assert third is None
    assert len(store.list_all()) == 1


def test_add_from_finding_different_channels_are_not_deduped_together(store):
    finding_a = _FakeFinding(rule="roas_drop_critical", channel="Meta Ads")
    finding_b = _FakeFinding(rule="roas_drop_critical", channel="Google Ads")

    opp_a = store.add_from_finding("Acme Corp", finding_a)
    opp_b = store.add_from_finding("Acme Corp", finding_b)

    assert opp_a is not None
    assert opp_b is not None
    assert len(store.list_all()) == 2


def test_add_from_finding_reappears_after_shipped(store):
    """Once a recurring issue's entry is marked shipped (i.e. actually
    fixed), a NEW occurrence of the same finding should be allowed to
    re-enter the queue -- shipped means 'was fixed', not 'can never be
    logged again if it recurs.'"""
    finding = _FakeFinding(rule="spend_spike_no_revenue", channel="LinkedIn Ads")

    first = store.add_from_finding("Acme Corp", finding)
    store.update_status(first.id, "shipped")

    second = store.add_from_finding("Acme Corp", finding)
    assert second is not None
    assert second.id != first.id


def test_add_from_finding_different_clients_are_not_deduped_together(store):
    finding = _FakeFinding(rule="roas_drop_critical", channel="Meta Ads")

    opp_acme = store.add_from_finding("Acme Corp", finding)
    opp_beta = store.add_from_finding("Beta Inc", finding)

    assert opp_acme is not None
    assert opp_beta is not None
    assert opp_acme.department != opp_beta.department
