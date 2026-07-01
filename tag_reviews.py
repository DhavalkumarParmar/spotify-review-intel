"""
Pass 1: per-review structured tagging via batched Gemini Flash calls.

Reads data/all_reviews.jsonl, writes data/tagged.jsonl. Resumable: skips
review IDs already present in tagged.jsonl unless --fresh is passed.

Usage:
    python tag_reviews.py --limit 20      # quick quality check
    python tag_reviews.py                 # full run
    python tag_reviews.py --fresh         # ignore existing tagged.jsonl, retag everything
"""
import argparse
import json
import logging
import os

from dotenv import load_dotenv

from llm_client import call_gemini, request_count

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                     handlers=[logging.StreamHandler(), logging.FileHandler("data/pipeline.log")])
logger = logging.getLogger("tag_reviews")

IN_PATH = "data/all_reviews.jsonl"
OUT_PATH = "data/tagged.jsonl"
REQUEST_COUNT_PATH = "data/.pass1_request_count.json"
BATCH_SIZE = 8
PASS1_MODEL = os.environ.get("PASS1_MODEL", "gemini-2.5-flash")

THEME_VOCAB = [
    "stuck_in_rut", "repetitive_recommendations", "discover_weekly_quality",
    "release_radar_quality", "algorithm_bias_toward_favorites",
    "cannot_escape_past_taste", "mood_context_missing", "no_serendipity",
    "too_mainstream", "too_niche", "poor_new_artist_surface",
    "autoplay_issues", "cold_start", "trust_in_recs", "explanation_missing",
    "other",
]
SEGMENT_VOCAB = [
    "long_tenure_user", "power_user", "casual_listener", "genre_specialist",
    "mood_based_listener", "new_user",
]
SENTIMENT_VOCAB = ["positive", "negative", "mixed", "neutral"]

TAG_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING", "description": "must exactly match the review's given id"},
            "is_relevant": {"type": "BOOLEAN"},
            "sentiment": {"type": "STRING", "enum": SENTIMENT_VOCAB},
            "themes": {"type": "ARRAY", "items": {"type": "STRING", "enum": THEME_VOCAB}},
            "user_segment_signals": {"type": "ARRAY", "items": {"type": "STRING", "enum": SEGMENT_VOCAB}},
            "job_to_be_done": {"type": "STRING"},
            "frustration_root_cause": {"type": "STRING"},
            "direct_quote": {"type": "STRING"},
        },
        "required": [
            "id", "is_relevant", "sentiment", "themes", "user_segment_signals",
            "job_to_be_done", "frustration_root_cause", "direct_quote",
        ],
    },
}

PROMPT_TEMPLATE = """You are analyzing Spotify user feedback for a Product Management research project \
about why users struggle to discover new music (Spotify's Growth Team goal: increase meaningful \
music discovery, reduce repetitive listening).

For EACH review below, return one JSON object with these fields:
- id: copy the review's id exactly as given
- is_relevant: true only if the review is actually about music discovery/recommendations \
(Discover Weekly, Release Radar, algorithm, repetitive suggestions, finding new artists, etc). \
false for reviews about ads, pricing, bugs, UI, payment, unrelated topics.
- sentiment: one of positive, negative, mixed, neutral
- themes: array of zero or more from this exact controlled vocabulary: {themes}
- user_segment_signals: array of zero or more from this exact controlled vocabulary: {segments}
- job_to_be_done: one short sentence, in the user's own voice, describing what they were trying \
to accomplish (e.g. "I'm trying to find new artists similar to what I already love"). If not \
relevant/inferable, use an empty string.
- frustration_root_cause: one short sentence describing the underlying cause of their frustration. \
Empty string if not relevant or not negative.
- direct_quote: the single most useful verbatim snippet from the review text (max 25 words), \
useful for a PM deck. Empty string if not relevant.

Only use themes/segments from the given vocabularies - do not invent new values.

Reviews:
{reviews_block}

Return a JSON array with exactly one tagged object per review, in any order, matching each by id."""


def load_jsonl(path: str) -> list:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_tagged_ids(path: str) -> set:
    if not os.path.exists(path):
        return set()
    return {t["id"] for t in load_jsonl(path)}


def build_prompt(batch: list) -> str:
    reviews_block = "\n".join(f"- id={r['id']}: \"{r['text'][:1000]}\"" for r in batch)
    return PROMPT_TEMPLATE.format(
        themes=", ".join(THEME_VOCAB),
        segments=", ".join(SEGMENT_VOCAB),
        reviews_block=reviews_block,
    )


def tag_batch(batch: list) -> list:
    prompt = build_prompt(batch)
    try:
        results = call_gemini(prompt, model=PASS1_MODEL, response_schema=TAG_SCHEMA)
    except Exception as e:
        logger.error(f"Batch failed entirely (ids={[r['id'] for r in batch]}): {e}")
        return []

    batch_ids = {r["id"] for r in batch}
    valid = [r for r in results if r.get("id") in batch_ids]
    missing = batch_ids - {r["id"] for r in valid}
    if missing:
        logger.warning(f"Model omitted {len(missing)} ids from batch: {missing}")
    return valid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max reviews to tag (for testing)")
    parser.add_argument("--fresh", action="store_true", help="Ignore existing tagged.jsonl, retag everything")
    args = parser.parse_args()

    reviews = load_jsonl(IN_PATH)
    if args.limit:
        reviews = reviews[:args.limit]

    already_tagged = set() if args.fresh else load_tagged_ids(OUT_PATH)
    todo = [r for r in reviews if r["id"] not in already_tagged]
    logger.info(f"{len(reviews)} reviews total, {len(already_tagged)} already tagged, {len(todo)} to do")

    if not todo:
        logger.info("Nothing to do.")
        return

    n_batches = (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE
    logger.info(f"Estimated Gemini requests for this run: {n_batches} (model={PASS1_MODEL})")

    mode = "a" if not args.fresh and os.path.exists(OUT_PATH) else "w"
    with open(OUT_PATH, mode) as f:
        for i in range(0, len(todo), BATCH_SIZE):
            batch = todo[i:i + BATCH_SIZE]
            logger.info(f"Tagging batch {i // BATCH_SIZE + 1}/{n_batches} ({len(batch)} reviews)")
            tagged = tag_batch(batch)
            for t in tagged:
                f.write(json.dumps(t) + "\n")
            f.flush()

    # request_count() is per-process; synthesize.py runs as a separate subprocess
    # in the full pipeline and can't see this, so persist it for last_run_metadata.json
    with open(REQUEST_COUNT_PATH, "w") as f:
        json.dump({"pass1_requests": request_count(PASS1_MODEL)}, f)

    logger.info(f"Done. Total Gemini requests made this session: {request_count()}")


if __name__ == "__main__":
    main()
