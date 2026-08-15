"""
LLM-based vacancy scoring using Google Gemini Flash (free tier).

Runs AFTER the keyword scraper. Reads vacancies from data/vacancies.jsonl,
sends each relevant one to Gemini for a second opinion, and stores the
LLM score alongside the keyword score.

Usage:
    python llm_scorer.py                # score all unscored vacancies
    python llm_scorer.py --rescore      # re-score everything
    python llm_scorer.py --max 5        # limit to 5 (for testing)
"""

import argparse
import json
import os
import re
import time
from pathlib import Path

try:
    import google.generativeai as genai
except ImportError:
    raise SystemExit(
        "google-generativeai not installed. Run: pip install google-generativeai"
    )

from profile import PROFILE_SUMMARY

# ── Config ──────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
VACANCIES_FILE = DATA_DIR / "vacancies.jsonl"
MIN_KEYWORD_SCORE = 5  # only send vacancies that passed keyword filter

API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-2.0-flash"  # free tier
RATE_LIMIT_DELAY = 4  # seconds between calls (free tier: 15 RPM)

SYSTEM_PROMPT = f"""You are an academic career advisor. You evaluate PhD vacancy descriptions
for a specific candidate and return a relevance score.

CANDIDATE PROFILE:
{PROFILE_SUMMARY}

The candidate is interested in:
- Data journalism and computational journalism
- Computational social science
- AI governance, algorithmic accountability, platform regulation
- Misinformation, disinformation, media manipulation
- Digital methods, NLP, text mining applied to media/politics
- Public interest technology, civic tech
- Security/society topics: extremism, radicalization, human rights (with data/tech angle)

The candidate is NOT interested in:
- Pure engineering, hard sciences, medical research
- Positions requiring a background they don't have (e.g. law degree, medical degree)
- Purely theoretical philosophy without empirical/computational component

SCORING RULES:
- Return a score from 0 to 100
- 80-100: Excellent fit — core research area, strong overlap with skills
- 60-79: Good fit — related field, candidate could contribute meaningfully
- 40-59: Possible fit — tangential connection, worth a look
- 20-39: Weak fit — only superficial keyword overlap
- 0-19: Not relevant

RESPONSE FORMAT:
Return ONLY a JSON object with these fields:
{{"score": <int 0-100>, "reason": "<one sentence explaining the score>"}}

No other text. No markdown fencing. Just the JSON object."""


def score_with_llm(vacancy: dict, model) -> dict:
    """Send a vacancy to Gemini and get an LLM score."""
    prompt = f"""VACANCY TITLE: {vacancy['title']}

EMPLOYER: {vacancy.get('employer', 'Unknown')}

RESEARCH FIELDS: {vacancy.get('research_fields', 'Not specified')}

DESCRIPTION (first 3000 chars):
{vacancy.get('description', '')[:3000]}

Score this vacancy for the candidate described in your instructions."""

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        # Extract JSON from response (handle markdown fences if present)
        if "```" in text:
            text = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            text = text.group(1) if text else ""
        result = json.loads(text)
        return {
            "llm_score": int(result.get("score", 0)),
            "llm_reason": result.get("reason", ""),
        }
    except (json.JSONDecodeError, ValueError, AttributeError) as e:
        print(f"    ⚠ Parse error: {e} — raw: {text[:200] if 'text' in dir() else 'no response'}")
        return {"llm_score": -1, "llm_reason": f"Parse error: {e}"}
    except Exception as e:
        print(f"    ✗ API error: {e}")
        return {"llm_score": -1, "llm_reason": f"API error: {e}"}


def main():
    parser = argparse.ArgumentParser(description="LLM scorer for PhD vacancies")
    parser.add_argument("--rescore", action="store_true", help="Re-score all vacancies")
    parser.add_argument("--max", type=int, default=0, help="Max vacancies to score")
    args = parser.parse_args()

    if not API_KEY:
        raise SystemExit("GEMINI_API_KEY environment variable not set.")

    if not VACANCIES_FILE.exists():
        raise SystemExit(f"No vacancies file at {VACANCIES_FILE}")

    # Load all vacancies
    lines = VACANCIES_FILE.read_text().strip().split("\n")
    vacancies = [json.loads(line) for line in lines if line.strip()]
    print(f"Loaded {len(vacancies)} total vacancies")

    # Filter to relevant ones (passed keyword scoring)
    relevant = [v for v in vacancies if v.get("score", 0) >= MIN_KEYWORD_SCORE]
    print(f"  {len(relevant)} passed keyword filter (score ≥ {MIN_KEYWORD_SCORE})")

    # Filter to unscored (unless --rescore)
    if not args.rescore:
        to_score = [v for v in relevant if v.get("llm_score") is None]
        print(f"  {len(to_score)} not yet LLM-scored")
    else:
        to_score = relevant
        print(f"  Re-scoring all {len(to_score)}")

    if args.max > 0:
        to_score = to_score[:args.max]
        print(f"  Limiting to {args.max}")

    if not to_score:
        print("Nothing to score.")
        return

    # Init Gemini
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel(MODEL, system_instruction=SYSTEM_PROMPT)

    # Score each vacancy
    scored_ids = {}
    for i, v in enumerate(to_score):
        slug = v["title"][:60]
        print(f"  [{i+1}/{len(to_score)}] {slug}...")

        result = score_with_llm(v, model)
        scored_ids[v["id"]] = result

        if result["llm_score"] >= 0:
            print(f"    → LLM: {result['llm_score']} — {result['llm_reason'][:80]}")
        else:
            print(f"    → Failed: {result['llm_reason'][:80]}")

        if i < len(to_score) - 1:
            time.sleep(RATE_LIMIT_DELAY)

    # Update vacancies file with LLM scores
    updated = []
    for v in vacancies:
        if v["id"] in scored_ids:
            v.update(scored_ids[v["id"]])
        updated.append(v)

    # Write back
    with open(VACANCIES_FILE, "w") as f:
        for v in updated:
            f.write(json.dumps(v) + "\n")

    scored_count = sum(1 for r in scored_ids.values() if r["llm_score"] >= 0)
    failed_count = sum(1 for r in scored_ids.values() if r["llm_score"] < 0)
    print(f"\n✓ LLM scoring complete: {scored_count} scored, {failed_count} failed")


if __name__ == "__main__":
    main()
