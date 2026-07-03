# Spotify Discovery Pain — Project Summary & Build Journey

This document captures the thought process, decisions, and real difficulties behind building
this tool.

**Live app**: https://spotify-review-intel.streamlit.app/
**Repo**: https://github.com/DhavalkumarParmar/spotify-review-intel

---

## 1. The Problem

Playing the role of a PM on Spotify's Growth Team: the strategic goal is increasing meaningful
music discovery and reducing repetitive listening. The job was to figure out **why users
struggle to discover new music**, by analyzing real user feedback at scale from multiple public
sources, then synthesizing insights a PM could act on — not just a scraper, but a full pipeline
from raw complaints to a demoable product.

This is Part 1 of a 4-part project.

## 2. System Architecture — Three Layers

```
Layer 1: Scrapers (5 sources)  -->  Layer 2: AI Analysis (2-pass)  -->  Layer 3: Streamlit App
```

**Why this shape:** each layer is independently resumable and cacheable. Scraping is slow and
rate-limited; AI tagging costs money/quota; synthesis needs the tagged data. Separating them
into flat scripts with file-based handoffs (`data/*.jsonl`) meant any single step could fail,
be interrupted, or be re-run without re-doing the others — which mattered a lot in practice
once budget and rate-limit constraints hit.

### Layer 1: Scrapers → `data/all_reviews.jsonl` (4,360 unique reviews)

| Source | Count | Method |
|---|---|---|
| Reddit (r/spotify, r/truespotify, r/SpotifyPlaylists) | 2,651 | Arctic Shift public archive API |
| App Store (US + India) | 850 | Apple's public RSS review feed |
| Google Play Store | 500 | `google-play-scraper` package |
| YouTube comments | 354 | Official YouTube Data API v3 |
| Spotify Community forum | 5 | Community's public LiQL search API |

Deliberately excluded per constraints: Twitter/X and TikTok (both block scrapers).

### Layer 2: Two-Pass AI Analysis

- **Pass 1 (tagging)**: every review gets tagged with `is_relevant`, `sentiment`, `themes`
  (controlled vocabulary), `user_segment_signals`, `job_to_be_done`, `frustration_root_cause`,
  and a `direct_quote` — via batched, schema-constrained LLM calls.
- **Pass 2 (synthesis)**: one call over the tagged dataset produces the PM-ready output —
  top themes, top segments, top jobs-to-be-done, top unmet needs, 10 powerful quotes, and 3
  root-cause hypotheses with evidence counts.

**Why two passes instead of one big prompt:** bulk per-review tagging is cheap/fast and doesn't
need a top-tier model; synthesis needs to reason across the whole dataset and benefits from a
better model. Splitting them also means Pass 1's expensive part (thousands of small calls) can
use a fast/free model while Pass 2 (one call) can afford a pricier one — this ended up mattering
a lot once real quota limits appeared (see Decisions section).

**Why a controlled vocabulary for themes/segments:** free-text categorization can't be reliably
charted or compared across thousands of reviews. Constraining the model to a fixed enum
(`stuck_in_rut`, `repetitive_recommendations`, `discover_weekly_quality`, etc.) makes the output
directly aggregable into the bar charts and percentages the Insights tab shows.

### Layer 3: Streamlit App (3 tabs)

1. **Insights** — the public dashboard: theme bar chart, user segment cards, JTBDs, 10 quote
   cards with source attribution, 3 root-cause hypotheses.
2. **Try the workflow** — lets any visitor paste a review (or click a pre-loaded sample) and get
   one live, real AI analysis back — proof the pipeline is real, without making them wait for a
   full scrape.
3. **Admin re-run** — password-gated; re-runs the entire scrape → tag → synthesize pipeline in a
   background thread, with a live log tail, without blocking the public tabs.

---

## 3. Final Results

- **4,360** unique reviews scraped across 5 sources
- **4,359** tagged (1 dropped by the model mid-batch, harmless/expected — see Difficulties)
- **1,684** (38.6%) judged relevant to music discovery/recommendations
- Pass 1 tagging done via a **local LLM** (Gemma-4-E2B via LM Studio) — 93 requests, $0 cost
- Pass 2 synthesis done via **Gemini 2.5 Flash** — 1 request, using the full relevant set

### Top findings (from the real synthesis)

**Top themes** (% of relevant reviews):
- `no_serendipity` — 36.9%
- `algorithm_bias_toward_favorites` — 28.8%
- `repetitive_recommendations` — 24.0%
- `discover_weekly_quality` — 16.3%
- `stuck_in_rut` — 13.8%

**Top user segments**: long-tenure users (28.7%), power users (18.3%), genre specialists (14.2%)

**Root cause hypotheses**:
1. Spotify's algorithms are overly optimized for engagement (listening duration) and
   reinforcing existing taste, rather than prioritizing genuine novelty or diverse discovery.
   (86 supporting reviews)
2. The shuffle and playback features are perceived as broken or non-random, leading to
   repetitive listening and hindering accidental discovery within existing libraries.
   (52 supporting reviews)
3. The influx of low-quality/AI-generated content in discovery playlists erodes user trust and
   makes it harder for legitimate new artists to be discovered. (27 supporting reviews)

**A quote worth putting on a slide**: *"Spotify's recommendation engine is not built to help
you discover music. It is built to keep you listening."*

---

## 4. Key Decisions & Why

| Decision | Reasoning |
|---|---|
| Flat scripts + JSONL files over a framework | No abstraction needed beyond what the task requires; every step independently resumable/debuggable; matches the "working code over elegant code" brief. |
| Structured/schema-constrained LLM output everywhere | Guarantees parseable JSON — no brittle regex over free-text model output. |
| Shuffle merged reviews (fixed seed) before any `--limit` cap | Without it, capping would grab all App Store/Play Store reviews first (mostly about ads/pricing) before any Reddit/YouTube content ever got tagged — shuffling ensures a representative cross-source sample regardless of cap size. |
| Resumable tagging (skip already-tagged IDs) | Essential once real API quotas and local compute constraints hit — let tagging be paused/resumed across many sessions without redoing work or losing progress. |
| Provider-agnostic LLM client (Gemini *or* local LM Studio) | Forced by a real budget constraint (see below) — turned into a genuine feature: same pipeline runs free-and-local or cloud-and-fast, and both were actually run and compared. |
| Comment-level (not video-level) filtering for YouTube | Video search results for "Spotify algorithm" style queries are dominated by artist/marketer growth-hacking content, not listener complaints — filtering the *comments* by keyword, plus excluding obviously promotional video titles, was the only way to get real listener pain rather than noise. |
| 3-tab Streamlit structure | Separates public consumption (Insights) from live credibility-proof (Try the workflow) from privileged control (Admin re-run) — each has a different audience and trust level. |

---

## 5. Difficulties Faced (the real build log)

This project hit a surprising number of real-world surprises — worth showing an evaluator that
the process was genuinely investigative, not just "call an API and done."

1. **The specified Gemini SDK (`google-generativeai`) is fully deprecated.** Google's own
   deprecation notice said "all support has ended" — had to discover this live and switch to the
   new unified `google-genai` SDK before writing any real code.

2. **The recommended App Store scraper library (`app-store-scraper`) is broken.** Unmaintained
   since 2020, it scrapes an auth token out of Apple's web page HTML — which has since changed
   structure, so the library silently returns zero reviews. Replaced it with a direct scraper
   against Apple's own public iTunes RSS review feed. That feed then turned out to have its own
   bug: the page number must be a **path segment** (`page=N/id=...`), not a query parameter
   (`?page=N` is silently ignored and always returns page 1) — found by noticing duplicate
   review IDs across "different" pages.

3. **Reddit blocks scraping entirely.** Reddit's own public JSON endpoints return a hard `403`
   from cloud/server environments (bot detection, not fixable with User-Agent tricks). Tried the
   official route next — but Reddit's current developer-app policy denied new script-app
   creation. Ultimately used **Arctic Shift**, a public, free, community-run mirror of Reddit's
   archived data (successor to Pushshift).

4. **An early Reddit scraper design had a real bug**, caught in a structured code review: a
   per-keyword comment-fetching cap meant a post's eligibility for comment-fetching depended on
   an arbitrary keyword-search-order artifact rather than any real ranking. Fixed by switching
   to a global cap instead.

5. **Spotify Community's search API has a confusing field name**: fetching all messages in a
   thread requires filtering on `topic.id`, not `conversation.id` — even though every message
   object *has* a `conversation.id` field, using it throws a validation error.

6. **Gemini's free tier is much stingier than published estimates suggest.** Testing live:
   `gemini-2.5-pro` (and every other Pro-tier model) has **zero** free-tier quota unless billing
   is linked to the Google Cloud project. `gemini-2.5-flash`'s real daily quota turned out to be
   **20 requests/day** — nowhere near the 250-500/day figures floating around online. This alone
   would have made tagging 4,000+ reviews take weeks on the free tier.

7. **No budget for billing** → pivoted to running Pass 1 (and optionally Pass 2) against a
   **local LLM via LM Studio** on a personal M1 MacBook Air — free, no quota, but introduced a
   new set of problems:
   - LM Studio runs on the user's own machine, not any cloud sandbox, so the actual tagging had
     to be run locally, with the pipeline code shared via git.
   - "Thinking"/reasoning local models (e.g. Qwen3 4B Thinking) sometimes returned **empty
     responses** — they spent their entire token budget on internal reasoning and never wrote
     the actual answer. Fixed by raising `max_tokens` substantially and detecting/reporting
     empty-content failures clearly instead of a cryptic JSON parse error.
   - Pass 2's synthesis prompt (aggregated data from hundreds of reviews) **exceeded small local
     models' context windows**, returning `400` errors that were initially misdiagnosed as a
     schema-support issue. Fixed by inspecting the actual error text for context-length language,
     and by making the evidence-set size configurable (`MAX_EVIDENCE_ITEMS`) so it can be shrunk
     to fit a small model's context window.
   - Local batched tagging occasionally **silently dropped a review's ID** from a batch response
     (smaller models are less precise at "return exactly N items" instructions) — handled by
     detecting and logging the gap; the dropped review just gets retried on the next run since
     it's resumable.

8. **YouTube video search results are dominated by artist/marketing content.** Searching
   "spotify algorithm" surfaces "how to grow your streams" videos for musicians, not listener
   complaints — even with complaint-phrased search queries. Fixed with comment-level keyword
   filtering plus a video-title exclusion list for obvious growth-hacking content, which
   substantially cleaned up the signal (first unfiltered test: 29/29 comments were off-topic
   artist-marketing chatter).

9. **GitHub push access was blocked twice** in the cloud build environment — once from the git
   CLI's credential relay, once from the GitHub App integration lacking write/branch-creation
   permission — both needed a permissions fix outside the coding session itself.

10. **Local git authentication also failed** on the user's Mac with "password authentication is
    not supported" — GitHub deprecated password-based git auth years ago; fixed with a Personal
    Access Token used as the git password.

11. **Streamlit Cloud's sharing setting** needed an explicit "make public" toggle — otherwise the
    deployed app silently redirected visitors to a login page instead of showing the app.

12. **A structured 8-angle code review** (correctness, reuse, simplification, efficiency,
    altitude, conventions) caught several real, non-obvious bugs before they reached production
    data — including the Reddit cap-scoping bug above, a Pass-2 model fallback that still
    defaulted to the zero-quota Pro model despite being "fixed" everywhere else, and a Streamlit
    session-state bug that could have let two admin sessions kick off conflicting pipeline runs
    simultaneously.

---

## 6. Local LLM vs. Cloud LLM — A Real Comparison

Because of the budget constraint, both a local (free) and cloud (Gemini) synthesis were
actually run over the same 1,684 relevant reviews (`data/synthesis_local.md` vs.
`data/synthesis.md` in the repo) — worth showing side-by-side in the deck:

| | Local (Gemma-4-E2B via LM Studio) | Cloud (Gemini 2.5 Flash) |
|---|---|---|
| Cost | $0 | Free tier (1 request) |
| Evidence reviews used | 40 (context-window limited) | 1,684 (all relevant reviews) |
| Jobs-to-be-done | Shallow, near-duplicate ("I want to find new music" ×several) | Well-clustered, specific, higher confidence counts |
| Quotes | Some duplicates, less curated | Clean, no duplicates, more "deck-ready" |
| Root cause evidence counts | 2-5 reviews each | 27-86 reviews each |

**Takeaway for the deck**: a small local model is a genuinely free, private way to do the bulk
per-review tagging (where volume matters more than nuance), but synthesis — reasoning across a
whole dataset — clearly benefits from a larger model's context window and reasoning quality.
The hybrid approach used here (local for Pass 1, cloud for Pass 2) captured the best of both.

---

## 7. Tech Stack

- **Language**: Python 3.11
- **AI**: Google Gemini API (`google-genai` SDK) and/or a local OpenAI-compatible server
  (LM Studio), via a provider-agnostic wrapper
- **UI**: Streamlit, deployed on Streamlit Community Cloud
- **Data sources**: Apple iTunes RSS, `google-play-scraper`, Arctic Shift (Reddit archive),
  Spotify Community's LiQL API, YouTube Data API v3
- **Storage**: flat JSONL files (no database — matches project scope and keeps everything
  git-diffable/inspectable)
- **Dev process**: iterative, with a structured multi-angle code review pass before finalizing

---

## 8. What Could Come Next (if there were more time)

- Additional App Store storefronts (UK, Brazil, Germany, etc.) for broader geographic signal
- Trustpilot as a 6th source (different user population than app stores)
- A confidence/coverage metric surfaced in the Insights tab (e.g. "X% of reviews successfully
  tagged" so data-quality gaps are visible in the product, not just the logs)
- Re-running Pass 1 fully on Gemini once/if a paid tier is available, to compare against the
  local-model tagging quality the same way Pass 2 was compared
