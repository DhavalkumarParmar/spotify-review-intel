"""
Provider-agnostic LLM client: Gemini API (cloud, free tier) or an
OpenAI-compatible local server like LM Studio (no cost, no quota, but you
run it yourself since it listens on your own machine, not this sandbox).

Both providers are called through call_llm(prompt, model, response_schema,
provider). response_schema is always standard JSON Schema (lowercase types:
"object"/"array"/"string"/"boolean"/"integer") - Gemini accepts this directly
via response_json_schema, and it's exactly the format OpenAI-compatible
servers (including LM Studio) expect too, so schemas are defined once and
used for either backend.

Provider selection: pass provider explicitly, or set LLM_PROVIDER env var
("gemini" or "lmstudio", default "gemini"). tag_reviews.py/synthesize.py also
support per-pass PASS1_PROVIDER/PASS2_PROVIDER overrides.

Note: the older `google-generativeai` package is fully deprecated by Google
("all support has ended") in favor of the unified `google-genai` SDK used here.
"""
import os
import time
import json
import logging

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError

load_dotenv()

logger = logging.getLogger("llm_client")

_client = None

# Minimum seconds between Gemini requests per model, keyed by substring match.
# Picked from free-tier RPM caps with a small safety margin. Not applied to
# the local provider, which has no rate limit - it's your own hardware.
_MIN_INTERVAL_BY_MODEL = {
    "flash": 6.5,   # ~9 RPM, under the ~10 RPM free-tier cap
    "pro": 13.0,    # ~4.6 RPM, under the ~5 RPM free-tier cap
}
_DEFAULT_MIN_INTERVAL = 13.0

_last_call_at = {}  # model -> last call timestamp
_request_counts = {}  # (provider, model) -> count of calls made this session

LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")


def _get_gemini_client():
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


def request_count(model: str = None, provider: str = None) -> int:
    """Requests made in the current process, optionally filtered by model/provider."""
    items = _request_counts.items()
    if model is not None:
        items = [(k, v) for k, v in items if k[1] == model]
    if provider is not None:
        items = [(k, v) for k, v in items if k[0] == provider]
    return sum(v for _, v in items)


def call_llm(prompt: str, model: str, response_schema: dict = None,
             provider: str = None, max_retries: int = 5):
    """
    Call an LLM with a text prompt, returning parsed JSON if response_schema
    is given (standard JSON Schema, lowercase types), else raw text.

    provider: "gemini" (default) or "lmstudio". Falls back to the LLM_PROVIDER
    env var, then "gemini".
    """
    provider = provider or os.environ.get("LLM_PROVIDER", "gemini")
    if provider == "lmstudio":
        return _call_lmstudio(prompt, model, response_schema, max_retries)
    return _call_gemini(prompt, model, response_schema, max_retries)


def _call_gemini(prompt: str, model: str, response_schema: dict, max_retries: int):
    client = _get_gemini_client()

    config_kwargs = {}
    if response_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_json_schema"] = response_schema
    config = types.GenerateContentConfig(**config_kwargs)

    last_error = None
    for attempt in range(1, max_retries + 1):
        _respect_rpm(model)
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
            _last_call_at[model] = time.time()
            _request_counts[("gemini", model)] = _request_counts.get(("gemini", model), 0) + 1

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


def _call_lmstudio(prompt: str, model: str, response_schema: dict, max_retries: int):
    """
    Calls a local LM Studio server (or any OpenAI-compatible /v1/chat/completions
    endpoint). No rate limiting - it's your own hardware. Tries strict JSON-schema
    mode first; falls back to plain json_object mode with the schema spelled out
    in the prompt if the loaded model/backend doesn't support schema-constrained
    decoding (common with smaller local models).
    """
    url = f"{LMSTUDIO_BASE_URL}/chat/completions"
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            if response_schema is not None:
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "response", "schema": response_schema, "strict": True},
                    },
                }
            else:
                payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}

            resp = requests.post(url, json=payload, timeout=180)

            if resp.status_code != 200 and response_schema is not None:
                # strict schema mode unsupported by this model/backend - retry once with
                # json_object mode and the schema spelled out in the prompt text instead
                logger.warning(f"LM Studio rejected json_schema mode (status={resp.status_code}), "
                                f"falling back to json_object mode with inline schema")
                fallback_prompt = (
                    f"{prompt}\n\nRespond with ONLY valid JSON matching this schema "
                    f"(no markdown, no explanation):\n{json.dumps(response_schema)}"
                )
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": fallback_prompt}],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                }
                resp = requests.post(url, json=payload, timeout=180)

            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            _request_counts[("lmstudio", model)] = _request_counts.get(("lmstudio", model), 0) + 1

            if response_schema is not None:
                return json.loads(content)
            return content

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_error = e
            if attempt == max_retries:
                logger.error(f"Could not reach LM Studio at {LMSTUDIO_BASE_URL} after "
                             f"{max_retries} attempts. Is LM Studio running with the local "
                             f"server started (Developer tab -> Start Server)? {e}")
                raise
            logger.warning(f"LM Studio unreachable, retrying (attempt {attempt}/{max_retries}): {e}")
            time.sleep(3)
        except (json.JSONDecodeError, KeyError) as e:
            last_error = e
            if attempt == max_retries:
                logger.error(f"LM Studio returned unparseable response after {max_retries} attempts: {e}")
                raise
            logger.warning(f"Invalid JSON from LM Studio, retrying (attempt {attempt}/{max_retries})")
            time.sleep(2)

    raise last_error
