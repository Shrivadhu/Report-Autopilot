import os
from unittest.mock import MagicMock, patch

import pytest
import anthropic

from report_autopilot.data_loader import load_csv
from report_autopilot.metrics import compare_periods
from report_autopilot.analyzer import (
    generate_narrative, generate_narrative_offline, AnalyzerError,
)


@pytest.fixture
def comparison():
    df = load_csv("sample_data/sample_campaign_data.csv")
    return compare_periods(df)


def test_offline_narrative_never_calls_api(comparison):
    """Offline mode must work with zero network access -- this is the
    fallback path a real outage depends on, so it must not accidentally
    depend on anything external."""
    narrative = generate_narrative_offline(comparison, "Acme Corp")
    assert "Acme Corp" in narrative
    assert len(narrative) > 50


def test_offline_narrative_never_claims_decline_on_positive_change(comparison):
    """Regression test for the wording bug caught during manual review:
    a channel that grew, just slower than others, must never be
    described as having 'declined'."""
    narrative = generate_narrative_offline(comparison, "Acme Corp")
    if comparison.biggest_decliner and comparison.biggest_decliner["pct_change"] >= 0:
        assert "decline" not in narrative.lower()


def test_generate_narrative_raises_analyzer_error_without_api_key(comparison, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AnalyzerError, match="ANTHROPIC_API_KEY"):
        generate_narrative(comparison, "Acme Corp")


def test_generate_narrative_wraps_auth_error(comparison, monkeypatch):
    """A real 401 from the API must come back as AnalyzerError (which the
    CLI knows how to fall back from), never as a raw SDK exception."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-definitely-invalid-test-key")
    with pytest.raises(AnalyzerError):
        generate_narrative(comparison, "Acme Corp")


def test_generate_narrative_wraps_connection_error(comparison, monkeypatch):
    """Simulate a network failure without hitting the real network, to
    confirm APIConnectionError is caught, retried, and eventually
    converted to AnalyzerError. time.sleep is mocked so this test runs
    instantly instead of waiting through the real backoff delays."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")
    monkeypatch.setattr("report_autopilot.analyzer.time.sleep", lambda s: None)

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = anthropic.APIConnectionError(
        request=MagicMock()
    )

    with patch("anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(AnalyzerError, match="unreachable|network|connection"):
            generate_narrative(comparison, "Acme Corp")
    # Confirms it actually retried (3 attempts) rather than failing on the first try
    assert mock_client.messages.create.call_count == 3


def test_generate_narrative_empty_response_raises(comparison, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")

    mock_message = MagicMock()
    mock_message.content = []  # no text blocks at all
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(AnalyzerError, match="empty response"):
            generate_narrative(comparison, "Acme Corp")


def test_generate_narrative_recovers_after_one_transient_failure(comparison, monkeypatch):
    """A single transient blip should not fail the whole call -- it
    should retry and succeed once the API responds normally."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")
    monkeypatch.setattr("report_autopilot.analyzer.time.sleep", lambda s: None)

    good_block = MagicMock()
    good_block.type = "text"
    good_block.text = "This is a good narrative."
    good_response = MagicMock()
    good_response.content = [good_block]

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        anthropic.APIConnectionError(request=MagicMock()),
        good_response,
    ]

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = generate_narrative(comparison, "Acme Corp")

    assert result == "This is a good narrative."
    assert mock_client.messages.create.call_count == 2


def test_generate_narrative_fails_fast_on_auth_error_no_retry(comparison, monkeypatch):
    """A 4xx auth error will never succeed on retry -- it must fail on
    the FIRST attempt, not waste time/quota retrying 3 times."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")
    monkeypatch.setattr("report_autopilot.analyzer.time.sleep", lambda s: None)

    resp = MagicMock()
    resp.status_code = 401
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = anthropic.APIStatusError(
        "invalid api key", response=resp, body={"error": {"message": "invalid api key"}}
    )

    with patch("anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(AnalyzerError, match="401"):
            generate_narrative(comparison, "Acme Corp")

    assert mock_client.messages.create.call_count == 1
