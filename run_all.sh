#!/usr/bin/env bash
# Runs the full pipeline end-to-end: scrape all 4 sources, merge, tag (Pass 1), synthesize (Pass 2).
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

echo "== 1/7 Scraping App Store =="
$PYTHON scrape_appstore.py

echo "== 2/7 Scraping Play Store =="
$PYTHON scrape_playstore.py

echo "== 3/7 Scraping Reddit (this is the slow one, rate-limited) =="
$PYTHON scrape_reddit.py

echo "== 4/7 Scraping Spotify Community =="
$PYTHON scrape_community.py

echo "== 5/7 Merging all sources =="
$PYTHON merge_reviews.py

echo "== 6/7 Pass 1: tagging reviews with Gemini Flash =="
$PYTHON tag_reviews.py $FRESH_FLAG

echo "== 7/7 Pass 2: synthesizing findings =="
$PYTHON synthesize.py

echo "Done. See data/synthesis.md for the human-readable summary."
