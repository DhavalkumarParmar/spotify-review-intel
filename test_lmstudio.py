"""
Quick sanity check for a local LM Studio connection before running the real
pipeline against it. Run this ON YOUR MAC (not in any remote sandbox), with
LM Studio open, the model loaded, and the local server started
(LM Studio -> Developer tab -> Start Server).

Usage:
    python test_lmstudio.py
    python test_lmstudio.py --model qwen3-4b-thinking-2507
"""
import argparse
import json
import sys

import requests

BASE_URL = "http://localhost:1234/v1"


def list_models():
    resp = requests.get(f"{BASE_URL}/models", timeout=10)
    resp.raise_for_status()
    return [m["id"] for m in resp.json()["data"]]


def test_structured_output(model: str):
    schema = {
        "type": "object",
        "properties": {
            "is_relevant": {"type": "boolean"},
            "sentiment": {"type": "string", "enum": ["positive", "negative", "mixed", "neutral"]},
            "themes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["is_relevant", "sentiment", "themes"],
    }
    prompt = (
        "Tag this Spotify review. Is it about music discovery/recommendations? "
        "What's the sentiment? Any themes?\n\n"
        "Review: \"I'm so tired of Discover Weekly playing the same 5 artists every single week.\""
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 4096,
        "response_format": {"type": "json_schema", "json_schema": {"name": "response", "schema": schema, "strict": True}},
    }
    resp = requests.post(f"{BASE_URL}/chat/completions", json=payload, timeout=120)
    if resp.status_code != 200:
        print(f"json_schema mode failed (status={resp.status_code}): {resp.text[:500]}")
        print("Falling back to json_object mode with inline schema instructions...")
        fallback_prompt = f"{prompt}\n\nRespond with ONLY valid JSON matching this schema:\n{json.dumps(schema)}"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": fallback_prompt}],
            "temperature": 0.2,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }
        resp = requests.post(f"{BASE_URL}/chat/completions", json=payload, timeout=120)
        resp.raise_for_status()

    raw = resp.json()
    message = raw["choices"][0]["message"]
    content = message.get("content", "")
    finish_reason = raw["choices"][0].get("finish_reason")

    print(f"finish_reason: {finish_reason}")
    if "reasoning_content" in message or "reasoning" in message:
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        print(f"Model's reasoning/thinking (first 500 chars): {str(reasoning)[:500]}")
    print("Raw content:", repr(content)[:1000])

    if not content.strip():
        print(
            "\nEMPTY CONTENT - this usually means the model spent its whole token budget "
            "'thinking' and never got to write the actual answer (common with reasoning/"
            "'Thinking' models on longer schemas). Try:\n"
            "  1. A non-thinking model instead (often more reliable for this kind of task), or\n"
            "  2. Increasing max_tokens further (edit this script/llm_client.py), or\n"
            "  3. In LM Studio's chat settings for this model, check if there's a way to see/limit "
            "reasoning length."
        )
        sys.exit(1)

    parsed = json.loads(content)
    print("Parsed OK:", parsed)
    return parsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default=None, help="Model id (defaults to first loaded model)")
    args = parser.parse_args()

    print(f"Checking LM Studio server at {BASE_URL} ...")
    try:
        models = list_models()
    except requests.exceptions.ConnectionError:
        print(f"ERROR: could not reach {BASE_URL}. Is LM Studio's local server running? "
              "(Developer tab -> Start Server)")
        sys.exit(1)

    print(f"Available models: {models}")
    model = args.model or (models[0] if models else None)
    if not model:
        print("ERROR: no models loaded in LM Studio. Load one first.")
        sys.exit(1)

    print(f"Testing structured output with model: {model}")
    test_structured_output(model)
    print("\nSuccess! Set PASS1_MODEL (and PASS1_PROVIDER=lmstudio) in your .env to this model id and run tag_reviews.py.")


if __name__ == "__main__":
    main()
