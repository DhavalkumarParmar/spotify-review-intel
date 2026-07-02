"""
Merges all per-source review JSONL files into data/all_reviews.jsonl,
deduping by (source, id).

Usage:
    python merge_reviews.py
"""
import json
import logging
import random

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                     handlers=[logging.StreamHandler(), logging.FileHandler("data/pipeline.log")])
logger = logging.getLogger("merge_reviews")

SOURCE_FILES = [
    "data/appstore.jsonl",
    "data/playstore.jsonl",
    "data/reddit.jsonl",
    "data/community.jsonl",
    "data/youtube.jsonl",
]
OUT_PATH = "data/all_reviews.jsonl"
REQUIRED_FIELDS = ["id", "source", "date", "rating", "text", "author", "url", "scraped_at"]


def load_jsonl(path: str) -> list:
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        logger.warning(f"{path} not found, skipping")
        return []


def main():
    seen = set()
    merged = []
    counts_by_source = {}

    for path in SOURCE_FILES:
        items = load_jsonl(path)
        added = 0
        for item in items:
            for field in REQUIRED_FIELDS:
                if field not in item:
                    item[field] = None
            key = (item["source"], item["id"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            added += 1
            counts_by_source[item["source"]] = counts_by_source.get(item["source"], 0) + 1
        logger.info(f"{path}: {len(items)} loaded, {added} unique added")

    # shuffle (fixed seed for reproducibility) so a --limit N in tag_reviews.py
    # samples across all sources instead of taking a prefix in file-concatenation
    # order (which would grab all App/Play Store reviews before any Reddit/Community)
    random.Random(42).shuffle(merged)

    with open(OUT_PATH, "w") as f:
        for item in merged:
            f.write(json.dumps(item) + "\n")

    logger.info(f"Wrote {len(merged)} unique reviews to {OUT_PATH}")
    logger.info(f"By source: {counts_by_source}")


if __name__ == "__main__":
    main()
