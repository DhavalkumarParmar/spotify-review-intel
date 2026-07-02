"""
Scrapes Spotify Google Play Store reviews via the `google-play-scraper` package.

Usage:
    python scrape_playstore.py --limit 20      # quick test
    python scrape_playstore.py                 # full run (up to 500)
"""
import argparse
import json
import logging
import time
from datetime import datetime, timezone

from google_play_scraper import Sort, reviews

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                     handlers=[logging.StreamHandler(), logging.FileHandler("data/pipeline.log")])
logger = logging.getLogger("scrape_playstore")

APP_ID = "com.spotify.music"
OUT_PATH = "data/playstore.jsonl"
PAGE_SIZE = 100
DEFAULT_LIMIT = 500


def normalize(entry: dict) -> dict:
    return {
        "id": f"playstore-{entry['reviewId']}",
        "source": "playstore",
        "date": entry["at"].isoformat() if hasattr(entry["at"], "isoformat") else str(entry["at"]),
        "rating": entry["score"],
        "text": entry["content"] or "",
        "author": entry["userName"] or "anonymous",
        "url": f"https://play.google.com/store/apps/details?id={APP_ID}&reviewId={entry['reviewId']}",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape(limit: int = None) -> list:
    target = limit if limit else DEFAULT_LIMIT
    all_reviews = []
    token = None

    while len(all_reviews) < target:
        batch, token = reviews(
            APP_ID,
            lang="en",
            country="us",
            sort=Sort.NEWEST,
            count=min(PAGE_SIZE, target - len(all_reviews)),
            continuation_token=token,
        )
        if not batch:
            logger.info("No more reviews returned, stopping")
            break
        all_reviews.extend(normalize(r) for r in batch)
        logger.info(f"Fetched {len(batch)} reviews (total {len(all_reviews)})")
        if token is None:
            logger.info("No continuation token, stopping")
            break
        time.sleep(1)  # be polite

    return all_reviews[:target]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max reviews (for testing)")
    args = parser.parse_args()

    review_list = scrape(limit=args.limit)

    with open(OUT_PATH, "w") as f:
        for r in review_list:
            f.write(json.dumps(r) + "\n")

    logger.info(f"Wrote {len(review_list)} reviews to {OUT_PATH}")


if __name__ == "__main__":
    main()
