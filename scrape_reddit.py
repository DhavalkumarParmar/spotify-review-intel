"""
Scrapes Reddit posts + top-level comments about Spotify discovery/recommendation
complaints from r/spotify, r/truespotify, r/SpotifyPlaylists over the last 6 months.

Reddit's own public JSON endpoints (reddit.com/*.json) return a hard 403 from this
environment (server-side bot detection, not a proxy/network-policy issue). Instead
this uses Arctic Shift (https://arctic-shift.photon-reddit.com), a public,
community-run mirror of Reddit's archived data (spiritual successor to Pushshift).
No auth needed, but it rate-limits aggressively, so calls are paced with sleeps
and retries.

Usage:
    python scrape_reddit.py --limit 20      # quick test (caps total items)
    python scrape_reddit.py                 # full run
"""
import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                     handlers=[logging.StreamHandler(), logging.FileHandler("data/pipeline.log")])
logger = logging.getLogger("scrape_reddit")

BASE_URL = "https://arctic-shift.photon-reddit.com/api"
USER_AGENT = "spotify-review-intel/1.0 (educational PM fellowship project)"
OUT_PATH = "data/reddit.jsonl"

SUBREDDITS = ["spotify", "truespotify", "SpotifyPlaylists"]
KEYWORDS = [
    "discover weekly", "release radar", "algorithm", "recommendations",
    "same songs", "stuck in a rut", "discovery", "repetitive", "new music",
]
MONTHS_BACK = 6
MAX_COMMENTS_PER_POST = 10
MAX_TOTAL_POSTS_TO_EXPAND = 350  # global cap on how many unique posts get comment-fetching, so a full run finishes in reasonable time
REQUEST_SLEEP = 4  # seconds between API calls, Arctic Shift rate-limits aggressively


def _get(path: str, params: dict, max_retries: int = 4) -> dict:
    url = f"{BASE_URL}{path}"
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                raise RuntimeError(data["error"])
            return data
        except Exception as e:
            if attempt == max_retries:
                logger.warning(f"Giving up on {path} {params}: {e}")
                return {"data": []}
            backoff = REQUEST_SLEEP * attempt
            logger.info(f"Retrying {path} after error ({e}), sleeping {backoff}s")
            time.sleep(backoff)
        finally:
            time.sleep(REQUEST_SLEEP)
    return {"data": []}


def search_posts(subreddit: str, query: str, after: str) -> list:
    data = _get("/posts/search", {
        "subreddit": subreddit,
        "query": query,
        "after": after,
        "sort": "desc",
        "limit": 100,
    })
    return data.get("data") or []


def fetch_top_level_comments(post_id: str) -> list:
    data = _get("/comments/search", {"link_id": f"t3_{post_id}", "limit": 100})
    comments = data.get("data") or []
    top_level = [
        c for c in comments
        if c.get("parent_id", "").startswith("t3_")
        and c.get("body") not in (None, "[removed]", "[deleted]")
        and c.get("author") not in (None, "[deleted]")
    ]
    top_level.sort(key=lambda c: c.get("score", 0), reverse=True)
    return top_level[:MAX_COMMENTS_PER_POST]


def normalize_post(post: dict) -> dict:
    subreddit = post.get("subreddit", "")
    post_id = post["id"]
    title = post.get("title", "") or ""
    selftext = post.get("selftext", "") or ""
    text = f"{title}. {selftext}".strip() if selftext else title
    return {
        "id": f"reddit-post-{post_id}",
        "source": f"reddit_r_{subreddit}",
        "date": datetime.fromtimestamp(post["created_utc"], tz=timezone.utc).isoformat(),
        "rating": None,
        "text": text,
        "author": post.get("author", "unknown"),
        "url": f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def normalize_comment(comment: dict, subreddit: str, post_id: str) -> dict:
    comment_id = comment["id"]
    return {
        "id": f"reddit-comment-{comment_id}",
        "source": f"reddit_r_{subreddit}",
        "date": datetime.fromtimestamp(comment["created_utc"], tz=timezone.utc).isoformat(),
        "rating": None,
        "text": comment.get("body", ""),
        "author": comment.get("author", "unknown"),
        "url": f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/comment/{comment_id}/",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape(limit: int = None) -> list:
    after_date = (datetime.now(timezone.utc) - timedelta(days=30 * MONTHS_BACK)).strftime("%Y-%m-%d")
    seen_post_ids = set()
    items = []
    posts_expanded_count = 0  # global counter, not reset per keyword/subreddit -
    # a per-keyword cap would make comment-expansion eligibility depend on which
    # keyword happened to surface a post first (since seen_post_ids means each
    # post is only evaluated once), silently dropping comments for posts that
    # rank well overall but not in the specific keyword search that found them first

    for subreddit in SUBREDDITS:
        for keyword in KEYWORDS:
            if limit and len(items) >= limit:
                break
            logger.info(f"[r/{subreddit}] searching '{keyword}'")
            posts = search_posts(subreddit, keyword, after_date)
            logger.info(f"[r/{subreddit}] '{keyword}': {len(posts)} posts found")

            for post in posts:
                if post["id"] in seen_post_ids:
                    continue
                seen_post_ids.add(post["id"])
                items.append(normalize_post(post))

                if limit and len(items) >= limit:
                    break

                if post.get("num_comments", 0) > 0 and posts_expanded_count < MAX_TOTAL_POSTS_TO_EXPAND:
                    posts_expanded_count += 1
                    comments = fetch_top_level_comments(post["id"])
                    for c in comments:
                        items.append(normalize_comment(c, subreddit, post["id"]))
                        if limit and len(items) >= limit:
                            break
                    logger.info(f"  post {post['id']}: +{len(comments)} top-level comments "
                                f"(total items {len(items)})")

            if limit and len(items) >= limit:
                break
        if limit and len(items) >= limit:
            break

    return items[:limit] if limit else items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max total items (posts+comments), for testing")
    args = parser.parse_args()

    items = scrape(limit=args.limit)

    with open(OUT_PATH, "w") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")

    logger.info(f"Wrote {len(items)} items to {OUT_PATH}")


if __name__ == "__main__":
    main()
