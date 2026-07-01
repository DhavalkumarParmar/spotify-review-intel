"""
Thin wrapper around the Gemini API (google-genai SDK).

Note: the older `google-generativeai` package is fully deprecated by Google
("all support has ended") in favor of the unified `google-genai` SDK used here.

Conservative free-tier RPM assumptions (checked live against
https://ai.google.dev/gemini-api/docs/rate-limits at build time):
  - gemini-2.5-flash: ~10 RPM / 500 RPD
  - gemini-2.5-pro:   ~5 RPM  / 100 RPD
We sleep enough between calls to stay under whichever model is in use.
"""
import os
import time
import json
import logging

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError

load_dotenv()

logger = logging.getLogger("llm_client")

_client = None

# Minimum seconds between requests per model, keyed by substring match.
# Picked from free-tier RPM caps with a small safety margin.
_MIN_INTERVAL_BY_MODEL = {
    "flash": 6.5,   # ~9 RPM, under the ~10 RPM free-tier cap
    "pro": 13.0,    # ~4.6 RPM, under the ~5 RPM free-tier cap
}
_DEFAULT_MIN_INTERVAL = 13.0

_last_call_at = {}  # model -> last call timestamp
_request_counts = {}  # model -> count of calls made this session


def _get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set (check your .env file)")
        _client = genai.Client(api_key=api_key)
    return _client


def _min_interval_for(model: str) -> float:
    for key, interval in _MIN_INTERVAL_BY_MODEL.items():
        if key in model:
            return interval
    return _DEFAULT_MIN_INTERVAL


def _respect_rpm(model: str):
    interval = _min_interval_for(model)
    last = _last_call_at.get(model)
    if last is not None:
        elapsed = time.time() - last
        wait = interval - elapsed
        if wait > 0:
            logger.info(f"Sleeping {wait:.1f}s to respect {model} RPM limit")
            time.sleep(wait)


def request_count(model: str = None) -> int:
    """Requests made in the current process, optionally filtered by model."""
    if model is None:
        return sum(_request_counts.values())
    return _request_counts.get(model, 0)


def call_gemini(prompt: str, model: str, response_schema: dict = None, max_retries: int = 5):
    """
    Call the Gemini API with a text prompt.

    If response_schema is given, requests structured JSON output and returns
    the already-parsed Python object (list/dict). Otherwise returns raw text.

    Retries on transient errors (429 rate limit, 500/503 server errors) with
    exponential backoff. Raises on non-transient errors (e.g. bad API key,
    invalid schema) after logging.
    """
    client = _get_client()

    config_kwargs = {}
    if response_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_schema"] = response_schema
    config = types.GenerateContentConfig(**config_kwargs)

    last_error = None
    for attempt in range(1, max_retries + 1):
        _respect_rpm(model)
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            _last_call_at[model] = time.time()
            _request_counts[model] = _request_counts.get(model, 0) + 1

            if response_schema is not None:
                return json.loads(resp.text)
            return resp.text

        except APIError as e:
            last_error = e
            _last_call_at[model] = time.time()
            status = getattr(e, "code", None)
            transient = status in (429, 500, 503) or status is None
            if not transient or attempt == max_retries:
                logger.error(f"Gemini call failed (attempt {attempt}/{max_retries}, status={status}): {e}")
                raise
            backoff = min(60, 2 ** attempt)
            logger.warning(f"Transient Gemini error (status={status}), retrying in {backoff}s "
                            f"(attempt {attempt}/{max_retries}): {e}")
            time.sleep(backoff)
        except json.JSONDecodeError as e:
            last_error = e
            if attempt == max_retries:
                logger.error(f"Gemini returned invalid JSON after {max_retries} attempts: {e}")
                raise
            logger.warning(f"Invalid JSON from Gemini, retrying (attempt {attempt}/{max_retries})")
            time.sleep(2 ** attempt)

    raise last_error
