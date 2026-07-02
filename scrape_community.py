"""
Scrapes 2-3 Spotify Community forum threads about discovery/recommendation
complaints, using the site's public LiQL search API (no auth needed).

Two-step process:
  1. Search several discovery-related keyword phrases for forum topic posts,
     score candidates by how many discovery-signal words appear in the
     subject, and pick the top N threads.
  2. For each picked thread, fetch every message in it (topic + all replies)
     via `topic.id = '<id>'` and normalize.

Usage:
    python scrape_community.py --threads 3      # default
    python scrape_community.py --thread-ids 7364945,7113061,7335166  # explicit
"""
import argparse
import json
import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                     handlers=[logging.StreamHandler(), logging.FileHandler("data/pipeline.log")])
logger = logging.getLogger("scrape_community")

BASE_URL = "https://community.spotify.com/api/2.0/search"
USER_AGENT = "spotify-review-intel/1.0 (educational PM fellowship project)"
OUT_PATH = "data/community.jsonl"
REQUEST_SLEEP = 2

SEARCH_KEYWORDS = [
    "recommendations", "algorithm", "repetitive", "discover weekly",
    "release radar", "discovery",
]
SIGNAL_WORDS = [
    "discover", "recommend", "algorithm", "repetitive", "same song",
    "stuck", "rut", "new music", "release radar", "discover weekly",
]
HTML_TAG_RE = re.compile(r"<[^>]+>")


def _liql(query: str) -> dict:
    encoded = urllib.parse.quote(query)
    resp = requests.get(f"{BASE_URL}?q={encoded}", headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    time.sleep(REQUEST_SLEEP)
    if data.get("status") != "success":
        logger.warning(f"LiQL query failed: {data.get('message')}")
        return {}
    return data


def strip_html(text: str) -> str:
    return HTML_TAG_RE.sub(" ", text or "").replace("&nbsp;", " ").strip()


def score_subject(subject: str) -> int:
    subject_lower = subject.lower()
    return sum(1 for w in SIGNAL_WORDS if w in subject_lower)


def find_candidate_threads(n: int) -> list:
    candidates = {}  # id -> (score, subject)
    for keyword in SEARCH_KEYWORDS:
        query = (
            "SELECT id, subject, message_type FROM messages "
            f"WHERE conversation.style = 'forum' AND subject MATCHES '{keyword}' "
            "ORDER BY post_time DESC LIMIT 15"
        )
        logger.info(f"Searching community for '{keyword}'")
        data = _liql(query)
        for item in data.get("data", {}).get("items", []):
            if item.get("message_type") != "forum_topic_message":
                continue
            score = score_subject(item["subject"])
            if score == 0:
                continue
            if item["id"] not in candidates or score > candidates[item["id"]][0]:
                candidates[item["id"]] = (score, item["subject"])

    ranked = sorted(candidates.items(), key=lambda kv: kv[1][0], reverse=True)
    logger.info(f"Top candidates: {[(tid, s, subj[:50]) for tid, (s, subj) in ranked[:n]]}")
    return [tid for tid, _ in ranked[:n]]


def fetch_thread(topic_id: str) -> list:
    query = (
        "SELECT id, subject, body, post_time, author.login, view_href, message_type "
        f"FROM messages WHERE topic.id = '{topic_id}' ORDER BY post_time ASC LIMIT 100"
    )
    data = _liql(query)
    return data.get("data", {}).get("items", [])


def normalize(msg: dict) -> dict:
    subject = msg.get("subject", "") or ""
    body = strip_html(msg.get("body", ""))
    text = f"{subject}. {body}".strip() if msg.get("message_type") == "forum_topic_message" else body
    return {
        "id": f"community-{msg['id']}",
        "source": "spotify_community",
        "date": msg.get("post_time", ""),
        "rating": None,
        "text": text,
        "author": msg.get("author", {}).get("login", "unknown"),
        "url": msg.get("view_href", ""),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape(thread_ids: list = None, n_threads: int = 3) -> list:
    if not thread_ids:
        thread_ids = find_candidate_threads(n_threads)

    items = []
    for topic_id in thread_ids:
        logger.info(f"Fetching thread {topic_id}")
        messages = fetch_thread(topic_id)
        logger.info(f"  {len(messages)} messages in thread {topic_id}")
        items.extend(normalize(m) for m in messages)

    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=3, help="Number of threads to auto-discover")
    parser.add_argument("--thread-ids", type=str, default=None,
                         help="Comma-separated explicit topic IDs, skips auto-discovery")
    args = parser.parse_args()

    thread_ids = args.thread_ids.split(",") if args.thread_ids else None
    items = scrape(thread_ids=thread_ids, n_threads=args.threads)

    with open(OUT_PATH, "w") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")

    logger.info(f"Wrote {len(items)} items to {OUT_PATH}")


if __name__ == "__main__":
    main()
