# spotify-review-intel

AI-powered analysis of Spotify user feedback about music discovery/recommendations,
built for a Product Management fellowship project. Scrapes reviews from 4 sources,
tags each with Gemini Flash (Pass 1), synthesizes findings with Gemini (Pass 2),
and serves the results through a public Streamlit app.

## What this does

1. **Scrape** Spotify user feedback about discovery/recommendation pain points from:
   - Apple App Store (US + India storefronts) - via Apple's public RSS review feed
   - Google Play Store - via the `google-play-scraper` package
   - Reddit (r/spotify, r/truespotify, r/SpotifyPlaylists) - via the Arctic Shift
     public archive API (Reddit's own JSON endpoints are blocked by their
     anti-bot protection from most server environments)
   - Spotify Community forum - via its public LiQL search API
2. **Tag** every review with Gemini Flash (Pass 1): relevance, sentiment, themes,
   user segment signals, job-to-be-done, root cause, and a standout quote.
3. **Synthesize** the tagged dataset into a PM-ready summary with Gemini (Pass 2):
   top themes, top user segments, top jobs-to-be-done, top unmet needs, the 10
   most powerful quotes, and 3 root-cause hypotheses.
4. **Serve** the results via a 3-tab Streamlit app: a public insights dashboard,
   a live single-review analyzer anyone can try, and a password-gated admin tab
   to re-run the full pipeline.

## Project layout

```
scrape_appstore.py    Apple App Store scraper -> data/appstore.jsonl
scrape_playstore.py   Google Play Store scraper -> data/playstore.jsonl
scrape_reddit.py      Reddit scraper (via Arctic Shift) -> data/reddit.jsonl
scrape_community.py   Spotify Community forum scraper -> data/community.jsonl
merge_reviews.py      Combines + dedupes all 4 sources -> data/all_reviews.jsonl
tag_reviews.py        Pass 1: Gemini Flash batched tagging -> data/tagged.jsonl
synthesize.py         Pass 2: Gemini synthesis -> data/synthesis.json, data/synthesis.md
llm_client.py         Thin Gemini API wrapper (retries, RPM throttling, request counter)
app.py                Streamlit app (3 tabs)
run_all.sh            Runs the full pipeline end-to-end
```

All intermediate and final data files live in `data/` and are committed to the
repo (not gitignored) so the deployed Streamlit app can read pre-computed
results without needing to run the pipeline itself.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env: paste your Gemini API key from https://aistudio.google.com/apikey,
# and set your own ADMIN_PASSWORD before deploying anywhere public
```

## Running end-to-end

```bash
./run_all.sh
```

Or run each step individually (each script also supports `--limit N` for
testing on a small sample first, and most support `--fresh` to bypass caching):

```bash
python scrape_appstore.py --limit 20     # quick test
python scrape_appstore.py                # full run

python scrape_playstore.py
python scrape_reddit.py                  # slow - rate-limited, can take 20-40+ min
python scrape_community.py

python merge_reviews.py

python tag_reviews.py --limit 20         # quick quality check
python tag_reviews.py                    # full run (resumable, skips already-tagged ids)
python tag_reviews.py --fresh            # force re-tag everything

python synthesize.py
```

Then view the results locally:

```bash
streamlit run app.py
```

## Gemini model / free tier notes

- Set `PASS1_MODEL` and `PASS2_MODEL` in `.env` to swap models.
- Default: both passes use `gemini-2.5-flash`. **Gemini Pro models have zero
  free-tier request quota unless your Google Cloud project has billing linked**
  (confirmed by testing `gemini-2.5-pro`, `gemini-pro-latest`, and
  `gemini-3.1-pro` - all return `limit: 0` on an unlinked free-tier key). If
  you link billing, Pro's own free-tier allowance still applies (no charge
  unless you exceed it) - you can then set `PASS2_MODEL=gemini-2.5-pro` for
  higher-quality synthesis.
- `llm_client.py` throttles requests conservatively (~9 RPM for Flash, ~4.6 RPM
  for Pro) and retries transient errors (429/500/503) with exponential backoff.
- `tag_reviews.py` batches 8 reviews per Gemini call and prints the estimated
  number of requests before running, so you can sanity-check against your
  daily quota before a large run.

## Data source notes / gotchas found while building this

- **Apple's RSS feed** (`itunes.apple.com/.../rss/customerreviews/...`) requires
  the page number as a **path segment** (`page=N/id=...`), not a query param
  (`?page=N` is silently ignored and always returns page 1). Caps at page 10
  (~500 reviews) per storefront.
- **Reddit's own JSON endpoints** (`reddit.com/*.json`) return a hard `403`
  from most server/cloud environments due to Reddit's bot detection - this is
  a server-side block, not fixable with User-Agent tricks. This project uses
  [Arctic Shift](https://arctic-shift.photon-reddit.com), a public, free,
  community-run mirror of Reddit's archived data instead.
- **Spotify Community's search API** uses `topic.id` (not `conversation.id`,
  despite `conversation.id` appearing in every message object) to fetch all
  messages in a thread.

## Streamlit Community Cloud deployment

1. Push this repo to GitHub (public repo required for the free tier).
2. Go to [share.streamlit.io](https://share.streamlit.io), connect your GitHub
   account, and deploy this repo with `app.py` as the entrypoint.
3. In the app's **Settings -> Secrets**, add (in TOML format):
   ```toml
   GOOGLE_API_KEY = "your_real_key_here"
   PASS1_MODEL = "gemini-2.5-flash"
   PASS2_MODEL = "gemini-2.5-flash"
   ADMIN_PASSWORD = "your_own_strong_password"
   ```
   Do **not** commit real secrets to `.env` in the repo - `.env` is gitignored;
   only `.env.example` (with placeholder values) is committed.
4. Confirm `data/*.jsonl`, `data/synthesis.json`, `data/synthesis.md`, and
   `data/last_run_metadata.json` are committed to the repo (they are not
   gitignored) - the deployed app reads these pre-computed files directly and
   does **not** re-run the scrape/tag/synthesize pipeline on every visit.
5. Anyone with the app URL can use Tab 1 (Insights) and Tab 2 (Try the
   workflow, which does make one live Gemini call per click). Only you (with
   the admin password) can trigger Tab 3's full pipeline re-run.
