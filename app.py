"""
Streamlit UI for the Spotify Discovery Pain research project.

Tab 1 (Insights) and Tab 2 (Try the workflow) are public. Tab 3 (Admin re-run)
is password-gated via the ADMIN_PASSWORD env var / Streamlit secret, and runs
the full scrape -> tag -> synthesize pipeline in a background thread so it
doesn't block the UI.

Run locally:
    streamlit run app.py
"""
import json
import os
import subprocess
import sys
import threading
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from tag_reviews import tag_batch, PASS1_PROVIDER, PASS1_MODEL

load_dotenv()

st.set_page_config(page_title="Spotify Discovery Pain", page_icon="🎧", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYNTHESIS_PATH = os.path.join(BASE_DIR, "data/synthesis.json")
METADATA_PATH = os.path.join(BASE_DIR, "data/last_run_metadata.json")
LOG_PATH = os.path.join(BASE_DIR, "data/pipeline.log")

SENTIMENT_COLORS = {
    "positive": "#1DB954",
    "negative": "#E22134",
    "mixed": "#F5A623",
    "neutral": "#8E8E8E",
}

SAMPLE_REVIEWS = {
    "Subtle complaint": (
        "It's fine I guess. I don't really discover much new stuff on here anymore, "
        "just the same artists I already listen to on repeat."
    ),
    "Angry rant": (
        "I am SO SICK of Discover Weekly. Every single week it's the SAME five artists "
        "recycled in a different order. I have listened to thousands of songs and it "
        "still can't figure out I want something NEW. Absolutely useless feature at "
        "this point, might as well delete it."
    ),
    "Mixed signal": (
        "Release Radar is hit or miss for me - sometimes it nails a great new artist, "
        "but half the songs are just remixes of stuff I already have saved. Wish it "
        "was more consistent."
    ),
    "Positive but with a request": (
        "Love Spotify overall, been a subscriber for years! Only thing I'd change is "
        "giving me a way to tell it 'more like this, less like that' directly on "
        "Discover Weekly instead of just thumbs up/down on individual songs."
    ),
    "Non-relevant": (
        "App keeps crashing every time I try to open a podcast episode on my Android "
        "phone. Please fix this, very frustrating."
    ),
}

# Process-global (not st.session_state) - the pipeline is a singleton resource
# shared across every visitor session on the same running Streamlit server, and
# is mutated from a background thread that has no session context of its own.
PIPELINE_STATE = {"status": "idle", "error": None}


@st.cache_data(ttl=30)
def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def render_insights_tab():
    synthesis = load_json(SYNTHESIS_PATH)
    metadata = load_json(METADATA_PATH)

    if not synthesis or not metadata:
        st.info(
            "No analysis results yet. Run the pipeline first (see README / Admin tab) "
            "to generate data/synthesis.json and data/last_run_metadata.json."
        )
        return

    last_run = metadata.get("last_run_at", "unknown")
    try:
        last_run_display = datetime.fromisoformat(last_run).strftime("%b %d, %Y %H:%M UTC")
    except Exception:
        last_run_display = last_run
    sources = ", ".join(metadata.get("reviews_by_source", {}).keys())
    st.caption(
        f"Last analyzed: {last_run_display} · "
        f"{metadata.get('total_reviews_scraped', '?')} reviews from {sources}"
    )

    st.title("🎧 Why Users Struggle to Discover New Music")

    st.subheader("Top Themes")
    themes_df = pd.DataFrame(synthesis.get("top_themes", []))
    if not themes_df.empty:
        chart = (
            alt.Chart(themes_df)
            .mark_bar(color="#1DB954")
            .encode(
                x=alt.X("pct:Q", title="% of relevant reviews"),
                y=alt.Y("theme:N", sort="-x", title=None),
                tooltip=["theme", "count", "pct"],
            )
            .properties(height=300)
        )
        st.altair_chart(chart, use_container_width=True)

    st.subheader("Top User Segments")
    segments = synthesis.get("top_segments", [])[:3]
    cols = st.columns(max(len(segments), 1))
    for col, seg in zip(cols, segments):
        with col:
            st.metric(seg["segment"].replace("_", " ").title(), f"{seg['pct']}%", f"{seg['count']} reviews")

    st.subheader("Top Jobs-to-be-Done")
    for j in synthesis.get("top_jtbds", []):
        st.markdown(f"- \"{j['jtbd']}\" _(~{j.get('frequency', '?')} reviews)_")

    st.subheader("Powerful Quotes")
    quote_cols = st.columns(2)
    for i, q in enumerate(synthesis.get("top_quotes", [])[:10]):
        with quote_cols[i % 2]:
            st.markdown(
                f"> {q['quote']}\n\n"
                f"— **{q.get('author', 'unknown')}**, _{q.get('source', 'unknown')}_ "
                f"[[source]]({q.get('url', '#')})"
            )

    st.subheader("Root Cause Hypotheses")
    for h in synthesis.get("root_cause_hypotheses", []):
        st.markdown(f"**{h['hypothesis']}**  \nEvidence: {h['evidence_count']} reviews")


def render_workflow_tab():
    st.title("Try the workflow")
    st.write(
        f"This runs one live call to {PASS1_MODEL} ({PASS1_PROVIDER}) - the same Pass 1 tagging "
        "logic used across the full dataset - on whatever review text you provide."
    )

    if "review_text" not in st.session_state:
        st.session_state.review_text = ""

    st.write("**Sample reviews:**")
    sample_cols = st.columns(len(SAMPLE_REVIEWS))
    for col, (label, text) in zip(sample_cols, SAMPLE_REVIEWS.items()):
        with col:
            if st.button(label, use_container_width=True):
                st.session_state.review_text = text

    review_text = st.text_area("Review text", key="review_text", height=120)

    if st.button("Analyze this review", type="primary"):
        if not review_text.strip():
            st.warning("Paste or select a review first.")
        else:
            with st.spinner(f"Calling {PASS1_MODEL}..."):
                try:
                    tagged = tag_batch([{"id": "adhoc", "text": review_text}])
                    if not tagged:
                        st.error("Analysis failed: the model didn't return a result for this review.")
                    else:
                        render_analysis_result(tagged[0])
                except Exception as e:
                    st.error(f"Analysis failed: {e}")


def render_analysis_result(result: dict):
    is_relevant = result.get("is_relevant", False)
    sentiment = result.get("sentiment", "neutral")
    color = SENTIMENT_COLORS.get(sentiment, "#8E8E8E")

    st.markdown("---")
    badge = "✅ Relevant to discovery" if is_relevant else "⛔ Not relevant to discovery"
    st.markdown(f"**{badge}**")
    st.markdown(
        f"**Sentiment:** <span style='color:{color}; font-weight:bold'>{sentiment}</span>",
        unsafe_allow_html=True,
    )

    themes = result.get("themes", [])
    if themes:
        st.markdown(
            "**Themes:** " + " ".join(
                f"<span style='background:#282828;color:white;border-radius:12px;"
                f"padding:3px 10px;margin-right:4px;font-size:0.85em'>{t}</span>"
                for t in themes
            ),
            unsafe_allow_html=True,
        )

    segments = result.get("user_segment_signals", [])
    if segments:
        st.markdown(f"**User segment signals:** {', '.join(segments)}")

    if result.get("job_to_be_done"):
        st.markdown(f"**Job to be done:**\n> {result['job_to_be_done']}")

    if result.get("frustration_root_cause"):
        st.markdown(f"**Frustration root cause:** {result['frustration_root_cause']}")

    if result.get("direct_quote"):
        st.markdown(f"**Best quote:** \"{result['direct_quote']}\"")


def run_pipeline_background():
    steps = [
        ("scraping_appstore", [sys.executable, "scrape_appstore.py"]),
        ("scraping_playstore", [sys.executable, "scrape_playstore.py"]),
        ("scraping_reddit", [sys.executable, "scrape_reddit.py"]),
        ("scraping_community", [sys.executable, "scrape_community.py"]),
        ("scraping_youtube", [sys.executable, "scrape_youtube.py"]),
        ("merging", [sys.executable, "merge_reviews.py"]),
        ("tagging", [sys.executable, "tag_reviews.py", "--fresh"]),
        ("synthesizing", [sys.executable, "synthesize.py"]),
    ]
    for name, cmd in steps:
        PIPELINE_STATE["status"] = name
        result = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            PIPELINE_STATE["status"] = "error"
            PIPELINE_STATE["error"] = f"Failed at {name}:\n{result.stderr[-3000:]}"
            return
    PIPELINE_STATE["status"] = "done"


def render_admin_tab():
    st.title("Admin: Re-run pipeline")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    entered = st.text_input("Password", type="password")

    if not admin_password or entered != admin_password:
        st.info("Enter the admin password to unlock pipeline controls.")
        return

    st.success("Unlocked.")
    status = PIPELINE_STATE["status"]
    st.write(f"**Pipeline state:** `{status}`")

    if status == "error":
        st.error(PIPELINE_STATE.get("error", "Unknown error"))

    running = status not in ("idle", "done", "error")
    if st.button("Re-run full pipeline", disabled=running):
        PIPELINE_STATE["status"] = "starting"
        PIPELINE_STATE["error"] = None
        thread = threading.Thread(target=run_pipeline_background, daemon=True)
        thread.start()
        st.rerun()

    st.caption(
        f"This re-scrapes all 5 sources (App Store, Play Store, Reddit, Spotify Community, YouTube), "
        f"retags everything with {PASS1_MODEL} ({PASS1_PROVIDER}), and re-synthesizes with "
        f"{os.environ.get('PASS2_MODEL', 'gemini-2.5-flash')} ({os.environ.get('PASS2_PROVIDER', 'gemini')}). "
        "The Reddit step is rate-limited and can take 20-40+ minutes. If using a local LM Studio "
        "provider, this admin server process must be able to reach it (same machine)."
    )

    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            lines = f.readlines()
        st.text_area("Pipeline log (tail)", "".join(lines[-60:]), height=300)

    if running:
        import time
        time.sleep(2)
        st.rerun()


def main():
    tab1, tab2, tab3 = st.tabs(["📊 Insights", "🧪 Try the workflow", "🔒 Admin re-run"])
    with tab1:
        render_insights_tab()
    with tab2:
        render_workflow_tab()
    with tab3:
        render_admin_tab()


if __name__ == "__main__":
    main()
