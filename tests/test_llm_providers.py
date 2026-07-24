from unittest.mock import MagicMock, patch

import pytest
import groq
from google.genai import errors as genai_errors

from report_autopilot.data_loader import load_csv
from report_autopilot.metrics import compare_periods
from report_autopilot.analyzer import AnalyzerError
from report_autopilot.llm_providers import (
    get_provider, ClaudeProvider, GeminiProvider, GroqProvider, OpenAIProvider,
    generate_narrative_with_fallback,
)


@pytest.fixture
def comparison():
    df = load_csv("sample_data/sample_campaign_data.csv")
    return compare_periods(df)


def test_get_provider_returns_correct_class():
    assert isinstance(get_provider("claude"), ClaudeProvider)
    assert isinstance(get_provider("gemini"), GeminiProvider)
    assert isinstance(get_provider("groq"), GroqProvider)
    assert isinstance(get_provider("openai"), OpenAIProvider)


def test_get_provider_unknown_name_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("not_a_real_provider")


# ---------- Claude (delegates to already-tested analyzer.py) ----------

def test_claude_provider_delegates_to_analyzer(comparison, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = ClaudeProvider()
    with pytest.raises(AnalyzerError, match="ANTHROPIC_API_KEY"):
        provider.generate_narrative(comparison, "Acme Corp")


# ---------- Gemini (real integration, mocked network) ----------

def test_gemini_provider_missing_key_raises(comparison, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = GeminiProvider()
    with pytest.raises(AnalyzerError, match="GEMINI_API_KEY"):
        provider.generate_narrative(comparison, "Acme Corp")


def test_gemini_provider_success(comparison, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    mock_response = MagicMock()
    mock_response.text = "This is a good Gemini narrative."
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client):
        result = GeminiProvider().generate_narrative(comparison, "Acme Corp")
    assert result == "This is a good Gemini narrative."


def test_gemini_provider_client_error_fails_fast_no_retry(comparison, monkeypatch):
    """A 4xx (bad key, bad request) should not be blind-retried 3 times
    -- it fails immediately, same principle as Claude's auth-error path."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = genai_errors.ClientError(
        code=401, response_json={"error": {"message": "invalid api key"}}
    )

    with patch("google.genai.Client", return_value=mock_client):
        with pytest.raises(AnalyzerError, match="client error"):
            GeminiProvider().generate_narrative(comparison, "Acme Corp")
    assert mock_client.models.generate_content.call_count == 1


def test_gemini_provider_server_error_retries_then_fails(comparison, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = genai_errors.ServerError(
        code=503, response_json={"error": {"message": "service unavailable"}}
    )

    with patch("google.genai.Client", return_value=mock_client), \
         patch("report_autopilot.llm_providers.time.sleep", lambda s: None):
        with pytest.raises(AnalyzerError, match="server error"):
            GeminiProvider().generate_narrative(comparison, "Acme Corp")
    assert mock_client.models.generate_content.call_count == 3  # confirms it actually retried


def test_gemini_provider_empty_response_raises(comparison, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    mock_response = MagicMock()
    mock_response.text = ""
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client):
        with pytest.raises(AnalyzerError, match="empty response"):
            GeminiProvider().generate_narrative(comparison, "Acme Corp")


# ---------- Groq (real integration, mocked network) ----------

def test_groq_provider_missing_key_raises(comparison, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    provider = GroqProvider()
    with pytest.raises(AnalyzerError, match="GROQ_API_KEY"):
        provider.generate_narrative(comparison, "Acme Corp")


def test_groq_provider_success(comparison, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")
    mock_message = MagicMock()
    mock_message.content = "This is a good Groq narrative."
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch("groq.Groq", return_value=mock_client):
        result = GroqProvider().generate_narrative(comparison, "Acme Corp")
    assert result == "This is a good Groq narrative."


def test_groq_provider_rate_limit_retries_then_fails(comparison, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    mock_client = MagicMock()
    mock_response = MagicMock(status_code=429)
    mock_client.chat.completions.create.side_effect = groq.RateLimitError(
        "rate limited", response=mock_response, body=None
    )

    with patch("groq.Groq", return_value=mock_client), \
         patch("report_autopilot.llm_providers.time.sleep", lambda s: None):
        with pytest.raises(AnalyzerError, match="rate limit"):
            GroqProvider().generate_narrative(comparison, "Acme Corp")
    assert mock_client.chat.completions.create.call_count == 3


def test_groq_provider_connection_error_retries_then_recovers(comparison, monkeypatch):
    """One transient blip should not fail the whole call if the retry
    succeeds -- mirrors the same property already proven for Claude."""
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    mock_message = MagicMock()
    mock_message.content = "Recovered narrative."
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    good_response = MagicMock()
    good_response.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        groq.APIConnectionError(request=MagicMock()),
        good_response,
    ]

    with patch("groq.Groq", return_value=mock_client), \
         patch("report_autopilot.llm_providers.time.sleep", lambda s: None):
        result = GroqProvider().generate_narrative(comparison, "Acme Corp")
    assert result == "Recovered narrative."
    assert mock_client.chat.completions.create.call_count == 2


# ---------- OpenAI (deliberately not implemented -- no free tier) ----------

def test_openai_provider_raises_not_implemented(comparison):
    with pytest.raises(NotImplementedError, match="not implemented"):
        OpenAIProvider().generate_narrative(comparison, "Acme Corp")


# ---------- fallback wrapper ----------

def test_fallback_wrapper_uses_offline_when_no_api_key(comparison, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = generate_narrative_with_fallback(comparison, "Acme Corp", provider_name="claude")
    assert "Acme Corp" in result


def test_fallback_wrapper_uses_offline_for_unimplemented_openai(comparison):
    result = generate_narrative_with_fallback(comparison, "Acme Corp", provider_name="openai")
    assert "Acme Corp" in result


def test_fallback_wrapper_uses_offline_for_missing_gemini_key(comparison, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = generate_narrative_with_fallback(comparison, "Acme Corp", provider_name="gemini")
    assert "Acme Corp" in result


def test_fallback_wrapper_unknown_provider_falls_back_not_crashes(comparison):
    result = generate_narrative_with_fallback(comparison, "Acme Corp", provider_name="not_real")
    assert "Acme Corp" in result
