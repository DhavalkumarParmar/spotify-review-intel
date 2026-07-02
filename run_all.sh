#!/usr/bin/env bash
# Runs the full pipeline end-to-end: scrape all 5 sources, merge, tag (Pass 1), synthesize (Pass 2).
#
# Usage:
#   ./run_all.sh            # normal run (skips already-tagged reviews on resume)
#   ./run_all.sh --fresh    # force re-tagging of everything in Pass 1
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

FRESH_FLAG=""
if [ "${1:-}" == "--fresh" ]; then
  FRESH_FLAG="--fresh"
fi

echo "== 1/8 Scraping App Store =="
$PYTHON scrape_appstore.py

echo "== 2/8 Scraping Play Store =="
$PYTHON scrape_playstore.py

echo "== 3/8 Scraping Reddit (this is the slow one, rate-limited) =="
$PYTHON scrape_reddit.py

echo "== 4/8 Scraping Spotify Community =="
$PYTHON scrape_community.py

echo "== 5/8 Scraping YouTube comments =="
$PYTHON scrape_youtube.py

echo "== 6/8 Merging all sources =="
$PYTHON merge_reviews.py

echo "== 7/8 Pass 1: tagging reviews =="
$PYTHON tag_reviews.py $FRESH_FLAG

echo "== 8/8 Pass 2: synthesizing findings =="
$PYTHON synthesize.py

echo "Done. See data/synthesis.md for the human-readable summary."
