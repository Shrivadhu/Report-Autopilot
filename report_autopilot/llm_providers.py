"""
llm_providers.py
-----------------
The JD names three LLMs explicitly: "LLMs (Claude, ChatGPT, Gemini)."
analyzer.py only ever called Claude directly. This adds the provider
abstraction so the pipeline isn't hardwired to one vendor.

Providers, and why each is what it is:
- Claude: real, tested, the default -- wraps analyzer.py's already-
  tested retry/fallback logic rather than duplicating it.
- Gemini: real, tested against the actual google-genai SDK's error
  types (mocked, since this sandbox can't reach Google's API, but the
  exception classes and call shape are the real ones, not guessed).
  Google AI Studio issues a genuinely free API key with no credit card
  (as of writing) -- https://aistudio.google.com/apikey
- Groq: real, tested the same way, against the real groq SDK (which is
  OpenAI-API-compatible). Groq's free tier hosts fast inference on open
  models (Llama 3.3, etc.) with no credit card required.
- OpenAI: kept as a documented stub, not implemented -- OpenAI does not
  currently offer a meaningful no-cost tier the way Gemini/Groq do, so
  it's deprioritized here rather than built out for a "make it free"
  request. The stub still documents exactly what real integration
  needs, same as before.

Every provider raises AnalyzerError on failure (never a raw SDK
exception) so callers -- cli.py, webhook_server.py -- have one uniform
error type to catch regardless of which vendor is behind it, and always
have a safe path to the offline templated fallback.
"""

import os
import time
import logging
from abc import ABC, abstractmethod

from report_autopilot.metrics import PeriodComparison
from report_autopilot.analyzer import (
    generate_narrative as _claude_generate_narrative,
    generate_narrative_offline,
    AnalyzerError,
    SYSTEM_PROMPT,
    _format_metrics_for_prompt,
)

logger = logging.getLogger("report_autopilot.llm_providers")


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate_narrative(self, comparison: PeriodComparison, client_name: str) -> str:
        """Returns the written narrative section for a report. Must
        raise AnalyzerError on failure -- callers already know how to
        catch that and fall back to generate_narrative_offline()."""
        raise NotImplementedError


class ClaudeProvider(LLMProvider):
    """Real, tested integration -- delegates to analyzer.py, which
    already has retries, error-wrapping, and 20+ tests covering its
    failure modes. This class adds zero new logic; it exists so Claude
    is selectable through the same interface as every other provider."""
    name = "claude"

    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model

    def generate_narrative(self, comparison: PeriodComparison, client_name: str) -> str:
        return _claude_generate_narrative(comparison, client_name, model=self.model)


class GeminiProvider(LLMProvider):
    """
    Real integration against Google's Gemini API via the current
    `google-genai` SDK (the old `google-generativeai` package is
    deprecated as of writing). Free tier: get a no-cost API key at
    https://aistudio.google.com/apikey -- no credit card required as of
    writing, subject to rate limits that can change.

    Requires: pip install google-genai, and GEMINI_API_KEY set.
    """
    name = "gemini"

    def __init__(self, model: str = "gemini-2.0-flash"):
        self.model = model

    def generate_narrative(self, comparison: PeriodComparison, client_name: str) -> str:
        try:
            from google import genai
            from google.genai import errors as genai_errors
            from google.genai.types import GenerateContentConfig
        except ImportError:
            raise AnalyzerError("The 'google-genai' package is not installed. Run: pip install google-genai")

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise AnalyzerError(
                "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey "
                "and set it in your environment or .env file."
            )

        client = genai.Client(api_key=api_key)
        metrics_block = _format_metrics_for_prompt(comparison)
        user_prompt = (
            f"Client name: {client_name}\n\n"
            f"Here are this week's computed metrics:\n\n{metrics_block}\n\n"
            f"Write the narrative section of this client's weekly report."
        )

        max_attempts = 3
        base_delay = 2
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = client.models.generate_content(
                    model=self.model,
                    contents=user_prompt,
                    config=GenerateContentConfig(system_instruction=SYSTEM_PROMPT, max_output_tokens=700),
                )
                text = (response.text or "").strip()
                if not text:
                    raise AnalyzerError("Gemini returned an empty response.")
                return text

            except genai_errors.ServerError as e:
                # 5xx -- transient, worth retrying
                last_error = e
                if attempt < max_attempts:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(f"Gemini server error (attempt {attempt}/{max_attempts}), retrying in {delay}s")
                    time.sleep(delay)
                    continue
                raise AnalyzerError(f"Gemini API server error after {max_attempts} attempts: {e}")

            except genai_errors.ClientError as e:
                # 4xx -- auth/bad-request/rate-limit, generally not
                # worth blind-retrying the same way a 5xx is; fail fast
                # so the fallback kicks in without wasting attempts.
                raise AnalyzerError(f"Gemini API returned a client error: {e}")

            except genai_errors.APIError as e:
                raise AnalyzerError(f"Gemini API call failed: {e}")
        raise AnalyzerError(f"Gemini API call failed after {max_attempts} attempts: {last_error}")


class GroqProvider(LLMProvider):
    """
    Real integration against Groq's API (OpenAI-compatible client),
    which serves fast inference on open-weight models. Free tier: get a
    no-cost API key at https://console.groq.com/keys -- no credit card
    required as of writing, subject to rate limits that can change.

    Requires: pip install groq, and GROQ_API_KEY set.
    """
    name = "groq"

    def __init__(self, model: str = "llama-3.3-70b-versatile"):
        self.model = model

    def generate_narrative(self, comparison: PeriodComparison, client_name: str) -> str:
        try:
            import groq
        except ImportError:
            raise AnalyzerError("The 'groq' package is not installed. Run: pip install groq")

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise AnalyzerError(
                "GROQ_API_KEY is not set. Get a free key at https://console.groq.com/keys "
                "and set it in your environment or .env file."
            )

        client = groq.Groq(api_key=api_key)
        metrics_block = _format_metrics_for_prompt(comparison)
        user_prompt = (
            f"Client name: {client_name}\n\n"
            f"Here are this week's computed metrics:\n\n{metrics_block}\n\n"
            f"Write the narrative section of this client's weekly report."
        )

        max_attempts = 3
        base_delay = 2
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    max_tokens=700,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                text = (response.choices[0].message.content or "").strip()
                if not text:
                    raise AnalyzerError("Groq returned an empty response.")
                return text

            except groq.RateLimitError as e:
                last_error = e
                if attempt < max_attempts:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(f"Groq rate limited (attempt {attempt}/{max_attempts}), retrying in {delay}s")
                    time.sleep(delay)
                    continue
                raise AnalyzerError(f"Groq API rate limit exceeded after {max_attempts} attempts: {e}")

            except (groq.APIConnectionError, groq.APITimeoutError) as e:
                last_error = e
                if attempt < max_attempts:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(f"Groq transient error (attempt {attempt}/{max_attempts}), retrying in {delay}s")
                    time.sleep(delay)
                    continue
                raise AnalyzerError(f"Groq API unreachable after {max_attempts} attempts: {e}")

            except groq.APIStatusError as e:
                raise AnalyzerError(f"Groq API returned an error (status {e.status_code}): {e}")

            except groq.APIError as e:
                raise AnalyzerError(f"Groq API call failed: {e}")
        raise AnalyzerError(f"Groq API call failed after {max_attempts} attempts: {last_error}")


class OpenAIProvider(LLMProvider):
    """
    NOT implemented -- deliberately deprioritized. Unlike Gemini and
    Groq, OpenAI does not currently offer a meaningful no-cost API tier
    (as of writing, new accounts get a small time-limited trial credit
    at most, not an ongoing free tier). Since the goal here was free
    providers specifically, effort went to Gemini/Groq instead. To
    implement this for real: pip install openai, port the same
    retry-classification pattern (openai.RateLimitError /
    APIConnectionError are transient, AuthenticationError /
    BadRequestError are not), map SYSTEM_PROMPT + the metrics block into
    chat.completions.create()'s message format.
    """
    name = "openai"

    def __init__(self, model: str = "gpt-4o"):
        self.model = model

    def generate_narrative(self, comparison: PeriodComparison, client_name: str) -> str:
        raise NotImplementedError(
            "OpenAIProvider is not implemented -- deprioritized since it has no free tier "
            "(unlike Gemini/Groq, which are real, tested integrations here). "
            "Use --llm-provider claude, gemini, or groq instead."
        )


_PROVIDERS = {
    "claude": ClaudeProvider,
    "gemini": GeminiProvider,
    "groq": GroqProvider,
    "openai": OpenAIProvider,
}


def get_provider(name: str, **kwargs) -> LLMProvider:
    if name not in _PROVIDERS:
        raise ValueError(f"Unknown provider {name!r}. Options: {list(_PROVIDERS)}")
    return _PROVIDERS[name](**kwargs)


def generate_narrative_with_fallback(comparison: PeriodComparison, client_name: str, provider_name: str = "claude") -> str:
    """
    Convenience function mirroring the CLI's existing behavior: try the
    requested provider, fall back to the offline templated narrative on
    ANY failure (including "this provider isn't implemented yet" or
    "no API key configured") so a scheduled report never fails to ship.
    """
    try:
        provider = get_provider(provider_name)
        return provider.generate_narrative(comparison, client_name)
    except (AnalyzerError, NotImplementedError, ValueError):
        return generate_narrative_offline(comparison, client_name)

