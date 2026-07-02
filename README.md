# spotify-review-intel

AI-powered analysis of Spotify user feedback about music discovery/recommendations,
built for a Product Management fellowship project. Scrapes reviews from 5 sources,
tags each review (Pass 1) and synthesizes findings (Pass 2) with an LLM (Gemini or
a local model via LM Studio), and serves the results through a public Streamlit app.

## What this does

1. **Scrape** Spotify user feedback about discovery/recommendation pain points from:
   - Apple App Store (US + India storefronts) - via Apple's public RSS review feed
   - Google Play Store - via the `google-play-scraper` package
   - Reddit (r/spotify, r/truespotify, r/SpotifyPlaylists) - via the Arctic Shift
     public archive API (Reddit's own JSON endpoints are blocked by their
     anti-bot protection from most server environments)
   - Spotify Community forum - via its public LiQL search API
   - YouTube comments - via the official YouTube Data API v3, filtered to
     comments that actually mention a discovery-related keyword (video search
     results skew toward artist/marketing content, so video titles alone
     aren't a reliable relevance signal)
2. **Tag** every review (Pass 1): relevance, sentiment, themes, user segment
   signals, job-to-be-done, root cause, and a standout quote.
3. **Synthesize** the tagged dataset into a PM-ready summary (Pass 2): top
   themes, top user segments, top jobs-to-be-done, top unmet needs, the 10
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
scrape_youtube.py     YouTube comments scraper (via YouTube Data API v3) -> data/youtube.jsonl
merge_reviews.py      Combines + dedupes all 5 sources -> data/all_reviews.jsonl
tag_reviews.py        Pass 1: batched LLM tagging -> data/tagged.jsonl
synthesize.py         Pass 2: LLM synthesis -> data/synthesis.json, data/synthesis.md
llm_client.py         Provider-agnostic LLM wrapper (Gemini or local LM Studio)
test_lmstudio.py      Standalone check that a local LM Studio connection works
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
python scrape_youtube.py                 # needs YOUTUBE_API_KEY in .env

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
- **The Flash free-tier daily quota is much lower than published estimates
  suggest**: a real run against a fresh API key hit `RESOURCE_EXHAUSTED` with
  `limit: 20` requests/day for `gemini-2.5-flash`. At 8 reviews/batch, that's
  only ~160 reviews/day for free - tagging a dataset of a few thousand reviews
  will take many days on the free tier alone, or requires billing linked, or
  use the local-LLM option below instead.
- `llm_client.py` throttles Gemini requests conservatively (~9 RPM for Flash,
  ~4.6 RPM for Pro) and retries transient errors (429/500/503) with
  exponential backoff.
- `tag_reviews.py` batches 8 reviews per call (`BATCH_SIZE` env var) and prints
  the estimated number of requests before running, so you can sanity-check
  against your daily quota before a large run.

## Running against a local LLM instead (no cost, no daily quota)

Both passes can run against a local OpenAI-compatible server (e.g.
[LM Studio](https://lmstudio.ai)) instead of Gemini - useful if you don't want
to link billing and the free tier's ~20 requests/day isn't enough.

**Important**: the local server runs on *your* machine. If you're running
this project's scripts in a remote/cloud sandbox (like a Claude Code on the
web session), that sandbox cannot reach `localhost` on your own computer -
you need to run `tag_reviews.py` / `synthesize.py` (and optionally
`streamlit run app.py`) directly on the same machine LM Studio is running on.

1. Open LM Studio, load a model (small local models like Gemma, Qwen3, or
   Nemotron in the 2-4B range work for this), and start the local server
   (**Developer tab -> Start Server** - default `http://localhost:1234`).
2. Test the connection first: `python test_lmstudio.py` (auto-detects the
   loaded model, tests structured JSON output, and tells you what to put in
   `.env`).
3. In `.env`, set:
   ```
   PASS1_PROVIDER=lmstudio
   PASS1_MODEL=<model id from test_lmstudio.py's output>
   PASS2_PROVIDER=lmstudio
   PASS2_MODEL=<same or a different loaded model>
   BATCH_SIZE=4
   ```
   Smaller local models are less reliable at following the batched-array
   tagging schema than Gemini Flash - start with a low `BATCH_SIZE` (3-4) and
   check `data/tagged.jsonl` quality with `--limit 20` before scaling up.
4. Run the pipeline as normal: `python tag_reviews.py --limit 20` to check
   quality, then `python tag_reviews.py` for the full run, then
   `python synthesize.py`.
5. You can mix providers per pass (e.g. `PASS1_PROVIDER=lmstudio` for the bulk
   tagging, `PASS2_PROVIDER=gemini` for the one synthesis call using your
   Gemini free tier, since synthesis is a single request regardless of
   dataset size).

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
- **YouTube video search results are dominated by artist/marketing content**
  ("how to grow on Spotify's algorithm") rather than listener complaints, even
  for listener-complaint-phrased queries - video titles alone are a poor
  relevance filter. `scrape_youtube.py` filters at the *comment* level
  (keyword match against comment text) and additionally skips videos whose
  title matches obvious artist-growth-hacking phrasing, which cleaned up the
  dataset significantly.

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
