"""
Scrapes Spotify App Store reviews via Apple's public iTunes RSS feed
(no auth/token needed - unlike the unmaintained `app-store-scraper` PyPI
package, whose token-scraping approach breaks against Apple's current
web page structure).

Feed caps at page 10 (~500 reviews per storefront) and repeats after that.

Usage:
    python scrape_appstore.py --limit 20      # quick test
    python scrape_appstore.py                 # full run (up to 500/storefront)
"""
import argparse
import json
import logging
import time
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                     handlers=[logging.StreamHandler(), logging.FileHandler("data/pipeline.log")])
logger = logging.getLogger("scrape_appstore")

SPOTIFY_APP_ID = 324684580
STOREFRONTS = ["us", "in"]
OUT_PATH = "data/appstore.jsonl"
USER_AGENT = "spotify-review-intel/1.0 (educational PM fellowship project)"
MAX_PAGES = 10  # Apple's feed repeats page 10 content beyond this


def fetch_page(storefront: str, app_id: int, page: int) -> list:
    # NOTE: page number must be a path segment, not a query param -
    # ?page=N is silently ignored by Apple's endpoint and always returns page 1.
    url = f"https://itunes.apple.com/{storefront}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    entries = data.get("feed", {}).get("entry", [])
    # First entry on page 1 is often the app summary, not a review - it lacks 'im:rating' + 'author'... actually
    # in practice all entries with 'im:rating' are real reviews; app metadata entry (if present) lacks it.
    return [e for e in entries if "im:rating" in e]


def normalize(entry: dict, storefront: str) -> dict:
    review_id = entry["id"]["label"]
    return {
        "id": f"appstore-{storefront}-{review_id}",
        "source": f"appstore_{storefront}",
        "date": entry["updated"]["label"],
        "rating": int(entry["im:rating"]["label"]),
        "text": f"{entry['title']['label']}. {entry['content']['label']}".strip(),
        "author": entry.get("author", {}).get("name", {}).get("label", "anonymous"),
        "url": entry.get("link", {}).get("attributes", {}).get("href", ""),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape(limit: int = None) -> list:
    all_reviews = []
    per_storefront_limit = limit if limit else 500

    for storefront in STOREFRONTS:
        storefront_reviews = []
        for page in range(1, MAX_PAGES + 1):
            if len(storefront_reviews) >= per_storefront_limit:
                break
            try:
                entries = fetch_page(storefront, SPOTIFY_APP_ID, page)
            except Exception as e:
                logger.warning(f"[{storefront}] page {page} failed: {e}")
                break
            if not entries:
                logger.info(f"[{storefront}] page {page} empty, stopping")
                break
            for entry in entries:
                storefront_reviews.append(normalize(entry, storefront))
            logger.info(f"[{storefront}] page {page}: +{len(entries)} reviews (total {len(storefront_reviews)})")
            time.sleep(1)  # be polite

        storefront_reviews = storefront_reviews[:per_storefront_limit]
        logger.info(f"[{storefront}] done: {len(storefront_reviews)} reviews")
        all_reviews.extend(storefront_reviews)

    return all_reviews


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max reviews per storefront (for testing)")
    args = parser.parse_args()

    reviews = scrape(limit=args.limit)

    with open(OUT_PATH, "w") as f:
        for r in reviews:
            f.write(json.dumps(r) + "\n")

    logger.info(f"Wrote {len(reviews)} reviews to {OUT_PATH}")


if __name__ == "__main__":
    main()
