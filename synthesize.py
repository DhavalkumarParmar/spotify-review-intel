"""
Pass 2: aggregate synthesis over the tagged dataset via a single Gemini Pro call.

Theme/segment frequency counts and % share are computed exactly in Python
(deterministic). Gemini Pro is used for the parts that need language
understanding: clustering free-text jobs-to-be-done, picking the most
powerful quotes, and proposing root-cause hypotheses with evidence counts.

Writes:
    data/synthesis.json
    data/synthesis.md
    data/last_run_metadata.json

Usage:
    python synthesize.py
"""
import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone

from dotenv import load_dotenv

from llm_client import call_gemini, request_count

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                     handlers=[logging.StreamHandler(), logging.FileHandler("data/pipeline.log")])
logger = logging.getLogger("synthesize")

TAGGED_PATH = "data/tagged.jsonl"
REVIEWS_PATH = "data/all_reviews.jsonl"
SYNTHESIS_JSON_PATH = "data/synthesis.json"
SYNTHESIS_MD_PATH = "data/synthesis.md"
METADATA_PATH = "data/last_run_metadata.json"
PASS2_MODEL = os.environ.get("PASS2_MODEL", "gemini-2.5-pro")
MAX_EVIDENCE_ITEMS = 400  # cap how many relevant reviews we feed into the Pass 2 prompt

SYNTHESIS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "top_jtbds": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "jtbd": {"type": "STRING"},
                    "frequency": {"type": "INTEGER"},
                },
                "required": ["jtbd", "frequency"],
            },
        },
        "top_unmet_needs": {"type": "ARRAY", "items": {"type": "STRING"}},
        "top_quotes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "quote": {"type": "STRING"},
                    "id": {"type": "STRING", "description": "the review id this quote came from"},
                },
                "required": ["quote", "id"],
            },
        },
        "root_cause_hypotheses": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "hypothesis": {"type": "STRING"},
                    "evidence_count": {"type": "INTEGER"},
                    "supporting_review_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["hypothesis", "evidence_count", "supporting_review_ids"],
            },
        },
    },
    "required": ["top_jtbds", "top_unmet_needs", "top_quotes", "root_cause_hypotheses"],
}

PROMPT_TEMPLATE = """You are synthesizing findings for a Spotify Growth Team PM researching why users \
struggle to discover new music. Below is structured, pre-tagged data from {n} relevant user reviews \
(App Store, Play Store, Reddit, Spotify Community) - each already tagged with themes, sentiment, and a \
job-to-be-done sentence. Theme and segment frequency counts have ALREADY been computed exactly and are \
given below for context; do not recompute them.

Theme frequency (already computed): {theme_counts}
User segment frequency (already computed): {segment_counts}

Your job, using ONLY the tagged data below (do not invent facts not present in the data):

1. top_jtbds: cluster similar "job to be done" sentences together and return the top 5 distinct clusters, \
each as one representative sentence with an estimated frequency (how many reviews expressed that job).
2. top_unmet_needs: the top 5 unmet needs implied by the frustration_root_cause and job_to_be_done fields \
(each a short phrase).
3. top_quotes: the 10 most powerful, PM-deck-worthy verbatim quotes from the direct_quote fields below. \
Return the exact quote text and the id of the review it came from (copy the id exactly).
4. root_cause_hypotheses: exactly 3 hypotheses for the root cause of the discovery problem. For each, give \
a one-sentence hypothesis, an evidence_count (how many reviews in the data support it), and a list of the \
supporting review ids (copy ids exactly, up to 8 examples per hypothesis).

Tagged review data (id | source | themes | segments | jtbd | root_cause | quote):
{evidence_block}
"""


def load_jsonl(path: str) -> list:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def build_evidence_block(tagged_relevant: list) -> str:
    lines = []
    for t in tagged_relevant:
        lines.append(
            f"- id={t['id']} | source={t.get('_source', 'unknown')} | themes={t['themes']} | "
            f"segments={t['user_segment_signals']} | jtbd=\"{t['job_to_be_done']}\" | "
            f"root_cause=\"{t['frustration_root_cause']}\" | quote=\"{t['direct_quote']}\""
        )
    return "\n".join(lines)


def main():
    tagged = load_jsonl(TAGGED_PATH)
    reviews = load_jsonl(REVIEWS_PATH)
    reviews_by_id = {r["id"]: r for r in reviews}

    relevant = [t for t in tagged if t.get("is_relevant")]
    for t in relevant:
        r = reviews_by_id.get(t["id"], {})
        t["_source"] = r.get("source", "unknown")
        t["_url"] = r.get("url", "")
        t["_author"] = r.get("author", "unknown")

    logger.info(f"{len(tagged)} tagged total, {len(relevant)} relevant")

    theme_counter = Counter()
    segment_counter = Counter()
    for t in relevant:
        theme_counter.update(t["themes"])
        segment_counter.update(t["user_segment_signals"])

    n = len(relevant)
    top_themes = [
        {"theme": theme, "count": count, "pct": round(100 * count / n, 1) if n else 0}
        for theme, count in theme_counter.most_common(10)
    ]
    top_segments_computed = [
        {"segment": seg, "count": count, "pct": round(100 * count / n, 1) if n else 0}
        for seg, count in segment_counter.most_common(5)
    ]

    evidence = relevant[:MAX_EVIDENCE_ITEMS]
    evidence_block = build_evidence_block(evidence)

    prompt = PROMPT_TEMPLATE.format(
        n=len(evidence),
        theme_counts=dict(theme_counter),
        segment_counts=dict(segment_counter),
        evidence_block=evidence_block,
    )

    logger.info(f"Calling {PASS2_MODEL} for synthesis over {len(evidence)} relevant reviews")
    llm_result = call_gemini(prompt, model=PASS2_MODEL, response_schema=SYNTHESIS_SCHEMA)

    # attach source/url/author to top_quotes and hypothesis evidence via review id lookup
    for q in llm_result.get("top_quotes", []):
        r = reviews_by_id.get(q.get("id"), {})
        q["source"] = r.get("source", "unknown")
        q["url"] = r.get("url", "")
        q["author"] = r.get("author", "unknown")

    synthesis = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_relevant_reviews": n,
        "total_reviews_analyzed": len(tagged),
        "top_themes": top_themes,
        "top_segments": top_segments_computed,
        "top_jtbds": llm_result.get("top_jtbds", []),
        "top_unmet_needs": llm_result.get("top_unmet_needs", []),
        "top_quotes": llm_result.get("top_quotes", []),
        "root_cause_hypotheses": llm_result.get("root_cause_hypotheses", []),
    }

    with open(SYNTHESIS_JSON_PATH, "w") as f:
        json.dump(synthesis, f, indent=2)
    logger.info(f"Wrote {SYNTHESIS_JSON_PATH}")

    write_markdown(synthesis)
    write_metadata(tagged, reviews)

    logger.info(f"Done. Total Gemini requests made this session: {request_count()}")


def write_markdown(s: dict):
    lines = [
        "# Spotify Discovery Pain — Synthesis",
        f"\n_Generated: {s['generated_at']}_",
        f"\n{s['total_relevant_reviews']} of {s['total_reviews_analyzed']} analyzed reviews were "
        f"relevant to music discovery/recommendations.\n",
        "## Top Themes",
    ]
    for t in s["top_themes"]:
        lines.append(f"- **{t['theme']}**: {t['count']} ({t['pct']}%)")

    lines.append("\n## Top User Segments")
    for seg in s["top_segments"]:
        lines.append(f"- **{seg['segment']}**: {seg['count']} ({seg['pct']}%)")

    lines.append("\n## Top Jobs-to-be-Done")
    for j in s["top_jtbds"]:
        lines.append(f"- \"{j['jtbd']}\" (~{j['frequency']} reviews)")

    lines.append("\n## Top Unmet Needs")
    for need in s["top_unmet_needs"]:
        lines.append(f"- {need}")

    lines.append("\n## Powerful Quotes")
    for q in s["top_quotes"]:
        lines.append(f"> \"{q['quote']}\"\n> — {q.get('author', 'unknown')}, {q.get('source', 'unknown')} "
                      f"([link]({q.get('url', '')}))\n")

    lines.append("## Root Cause Hypotheses")
    for h in s["root_cause_hypotheses"]:
        lines.append(f"- **{h['hypothesis']}** (evidence: {h['evidence_count']} reviews)")

    with open(SYNTHESIS_MD_PATH, "w") as f:
        f.write("\n".join(lines))
    logger.info(f"Wrote {SYNTHESIS_MD_PATH}")


def write_metadata(tagged: list, reviews: list):
    source_counts = Counter(r["source"] for r in reviews)
    metadata = {
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "total_reviews_scraped": len(reviews),
        "reviews_by_source": dict(source_counts),
        "total_reviews_tagged": len(tagged),
        "pass1_model": os.environ.get("PASS1_MODEL", "gemini-2.5-flash"),
        "pass2_model": PASS2_MODEL,
        "pass1_requests_this_session": request_count(os.environ.get("PASS1_MODEL", "gemini-2.5-flash")),
        "pass2_requests_this_session": request_count(PASS2_MODEL),
    }
    with open(METADATA_PATH, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info(f"Wrote {METADATA_PATH}")


if __name__ == "__main__":
    main()
