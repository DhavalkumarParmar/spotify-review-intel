"""
Scrapes YouTube comments about Spotify discovery/recommendation complaints,
via the official YouTube Data API v3 (needs YOUTUBE_API_KEY in .env - see
README for how to get a free key, 10,000 quota units/day).

Video search results skew toward artist/marketer content ("how to grow on
Spotify's algorithm") rather than listener complaints, so this filters at
the COMMENT level: only keeps comments whose text actually mentions a
discovery-related keyword, regardless of what the video itself is about.

Usage:
    python scrape_youtube.py --limit 20      # quick test
    python scrape_youtube.py                 # full run
"""
import argparse
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                     handlers=[logging.StreamHandler(), logging.FileHandler("data/pipeline.log")])
logger = logging.getLogger("scrape_youtube")

API_KEY = os.environ.get("YOUTUBE_API_KEY")
BASE_URL = "https://www.googleapis.com/youtube/v3"
OUT_PATH = "data/youtube.jsonl"
REQUEST_SLEEP = 0.5

SEARCH_QUERIES = [
    "spotify discover weekly", "spotify recommendations", "spotify algorithm",
    "spotify recommendations repetitive", "spotify algorithm same songs",
    "spotify discover weekly not working", "spotify release radar",
    "spotify algorithm sucks", "spotify recommendations bad",
    "spotify stuck in a rut", "why does spotify recommend",
]
MAX_VIDEOS_PER_QUERY = 8
MAX_COMMENTS_PER_VIDEO = 100  # fetched (pre-filter); most get filtered out

# Only comments actually mentioning one of these get kept - video titles
# alone are a poor relevance signal on YouTube for this topic.
COMMENT_KEYWORDS = [
    "discover weekly", "release radar", "algorithm", "recommend", "repetitive",
    "same song", "same artist", "stuck in a rut", "new music", "discover",
    "shuffle", "recommendation",
]
KEYWORD_RE = re.compile("|".join(re.escape(k) for k in COMMENT_KEYWORDS), re.IGNORECASE)

# Skip videos aimed at artists/marketers trying to get THEIR music discovered
# (growth-hacking advice) rather than listeners struggling to discover music -
# these dominate search results for "spotify algorithm" style queries and
# would otherwise pollute the dataset with off-topic (if keyword-matching) noise.
ARTIST_MARKETING_TITLE_TERMS = [
    "grow your", "get more streams", "get discovered", "playlist pitch",
    "spotify for artists", "music marketing", "artist growth", "crack the algorithm",
    "hack the algorithm", "algorithm love you", "get on release radar",
    "how to get playlisted", "monetize", "music promotion", "grow on spotify",
]
ARTIST_MARKETING_RE = re.compile("|".join(re.escape(k) for k in ARTIST_MARKETING_TITLE_TERMS), re.IGNORECASE)


def _get(path: str, params: dict) -> dict:
    params = {**params, "key": API_KEY}
    resp = requests.get(f"{BASE_URL}/{path}", params=params, timeout=30)
    time.sleep(REQUEST_SLEEP)
    if resp.status_code != 200:
        logger.warning(f"{path} failed (status={resp.status_code}): {resp.text[:300]}")
        return {}
    return resp.json()


def search_videos(query: str) -> list:
    data = _get("search", {
        "part": "snippet", "q": query, "type": "video",
        "maxResults": MAX_VIDEOS_PER_QUERY, "relevanceLanguage": "en",
    })
    results = []
    for item in data.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        if not video_id:
            continue
        title = item.get("snippet", {}).get("title", "")
        results.append((video_id, title))
    return results


def fetch_comments(video_id: str) -> list:
    comments = []
    page_token = None
    while len(comments) < MAX_COMMENTS_PER_VIDEO:
        params = {
            "part": "snippet", "videoId": video_id, "order": "relevance",
            "maxResults": 100, "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token
        data = _get("commentThreads", params)
        items = data.get("items", [])
        if not items:
            break
        for item in items:
            snippet = item["snippet"]["topLevelComment"]["snippet"]
            comments.append({
                "comment_id": item["snippet"]["topLevelComment"]["id"],
                "video_id": video_id,
                "text": snippet.get("textOriginal", ""),
                "author": snippet.get("authorDisplayName", "unknown"),
                "published_at": snippet.get("publishedAt", ""),
                "like_count": snippet.get("likeCount", 0),
            })
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return comments


def normalize(comment: dict) -> dict:
    return {
        "id": f"youtube-comment-{comment['comment_id']}",
        "source": "youtube",
        "date": comment["published_at"],
        "rating": None,
        "text": comment["text"],
        "author": comment["author"],
        "url": f"https://www.youtube.com/watch?v={comment['video_id']}&lc={comment['comment_id']}",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape(limit: int = None) -> list:
    if not API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is not set (check your .env file)")

    seen_video_ids = set()
    seen_comment_ids = set()
    items = []

    for query in SEARCH_QUERIES:
        if limit and len(items) >= limit:
            break
        logger.info(f"Searching YouTube for '{query}'")
        videos = search_videos(query)
        logger.info(f"'{query}': {len(videos)} videos found")

        for video_id, title in videos:
            if video_id in seen_video_ids:
                continue
            seen_video_ids.add(video_id)

            if ARTIST_MARKETING_RE.search(title):
                logger.info(f"  video {video_id} skipped (artist/marketing title): {title[:70]}")
                continue

            comments = fetch_comments(video_id)
            relevant = [c for c in comments if KEYWORD_RE.search(c["text"])]
            logger.info(f"  video {video_id}: {len(comments)} comments fetched, "
                        f"{len(relevant)} keyword-relevant")

            for c in relevant:
                if c["comment_id"] in seen_comment_ids:
                    continue
                seen_comment_ids.add(c["comment_id"])
                items.append(normalize(c))
                if limit and len(items) >= limit:
                    break
            if limit and len(items) >= limit:
                break
        if limit and len(items) >= limit:
            break

    return items[:limit] if limit else items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max comments (for testing)")
    args = parser.parse_args()

    items = scrape(limit=args.limit)

    with open(OUT_PATH, "w") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")

    logger.info(f"Wrote {len(items)} items to {OUT_PATH}")


if __name__ == "__main__":
    main()
