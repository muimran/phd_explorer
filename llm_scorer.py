"""
LLM-based vacancy scoring using Gemini and Groq (both free tier).

Runs AFTER the keyword scraper. Reads vacancies from data/vacancies.jsonl,
sends each relevant one to both LLMs for scoring, and stores the results
alongside the keyword score for side-by-side comparison.

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

from profile import PROFILE_SUMMARY

# ── Config ──────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
VACANCIES_FILE = DATA_DIR / "vacancies.jsonl"
MIN_KEYWORD_SCORE = 5  # only send vacancies that passed keyword filter

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

GEMINI_MODEL = "gemini-3-flash-preview"
GROQ_MODEL = "llama-3.3-70b-versatile"

GEMINI_DELAY = 8   # seconds between calls (free tier: 15 RPM)
GROQ_DELAY = 3     # seconds between calls (free tier: 30 RPM)

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


def build_prompt(vacancy: dict) -> str:
    return f"""VACANCY TITLE: {vacancy['title']}

EMPLOYER: {vacancy.get('employer', 'Unknown')}

RESEARCH FIELDS: {vacancy.get('research_fields', 'Not specified')}

DESCRIPTION (first 3000 chars):
{vacancy.get('description', '')[:3000]}

Score this vacancy for the candidate described in your instructions."""


def parse_llm_response(text: str) -> dict:
    """Parse JSON from an LLM response, handling markdown fences."""
    text = text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        text = m.group(1) if m else text
    result = json.loads(text)
    return {
        "score": int(result.get("score", 0)),
        "reason": result.get("reason", ""),
    }


# ── Gemini scorer ───────────────────────────────────────────────────────────

def score_with_gemini(vacancy: dict, client) -> dict:
    """Score using Google Gemini."""
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=build_prompt(vacancy),
            config={
                "system_instruction": SYSTEM_PROMPT,
                "temperature": 0.1,
            },
        )
        result = parse_llm_response(response.text)
        return {"gemini_score": result["score"], "gemini_reason": result["reason"]}
    except (json.JSONDecodeError, ValueError, AttributeError) as e:
        return {"gemini_score": -1, "gemini_reason": f"Parse error: {e}"}
    except Exception as e:
        return {"gemini_score": -1, "gemini_reason": f"API error: {e}"}


# ── Groq scorer ─────────────────────────────────────────────────────────────

def score_with_groq(vacancy: dict, client) -> dict:
    """Score using Groq (Llama 3.3 70B)."""
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(vacancy)},
            ],
            temperature=0.1,
            max_tokens=200,
        )
        result = parse_llm_response(response.choices[0].message.content)
        return {"groq_score": result["score"], "groq_reason": result["reason"]}
    except (json.JSONDecodeError, ValueError, AttributeError) as e:
        return {"groq_score": -1, "groq_reason": f"Parse error: {e}"}
    except Exception as e:
        return {"groq_score": -1, "groq_reason": f"API error: {e}"}


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LLM scorer for PhD vacancies")
    parser.add_argument("--rescore", action="store_true", help="Re-score all vacancies")
    parser.add_argument("--max", type=int, default=0, help="Max vacancies to score")
    args = parser.parse_args()

    if not GEMINI_API_KEY and not GROQ_API_KEY:
        print("No API keys set — skipping LLM scoring.")
        return

    if not VACANCIES_FILE.exists():
        print(f"No vacancies file at {VACANCIES_FILE}")
        return

    # Load all vacancies
    lines = VACANCIES_FILE.read_text().strip().split("\n")
    vacancies = [json.loads(line) for line in lines if line.strip()]
    print(f"Loaded {len(vacancies)} total vacancies")

    # Filter to relevant ones (passed keyword scoring)
    relevant = [v for v in vacancies if v.get("score", 0) >= MIN_KEYWORD_SCORE]
    print(f"  {len(relevant)} passed keyword filter (score ≥ {MIN_KEYWORD_SCORE})")

    # Init clients
    gemini_client = None
    groq_client = None

    if GEMINI_API_KEY:
        try:
            from google import genai
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            print("  ✓ Gemini client ready")
        except Exception as e:
            print(f"  ✗ Gemini init failed: {e}")

    if GROQ_API_KEY:
        try:
            from groq import Groq
            groq_client = Groq(api_key=GROQ_API_KEY)
            print("  ✓ Groq client ready")
        except Exception as e:
            print(f"  ✗ Groq init failed: {e}")

    if not gemini_client and not groq_client:
        print("No LLM clients available — skipping.")
        return

    # Determine what needs scoring
    if args.rescore:
        to_score = relevant
        print(f"  Re-scoring all {len(to_score)}")
    else:
        to_score = []
        for v in relevant:
            needs_gemini = gemini_client and (v.get("gemini_score") is None or v.get("gemini_score", 0) < 0)
            needs_groq = groq_client and (v.get("groq_score") is None or v.get("groq_score", 0) < 0)
            # Also pick up vacancies with old llm_score field (migration)
            needs_migration = "llm_score" in v and "gemini_score" not in v
            if needs_gemini or needs_groq or needs_migration:
                to_score.append(v)
        print(f"  {len(to_score)} need LLM scoring")

    if args.max > 0:
        to_score = to_score[:args.max]
        print(f"  Limiting to {args.max}")

    if not to_score:
        print("Nothing to score.")
        return

    # Score each vacancy with both LLMs
    scored_ids = {}
    for i, v in enumerate(to_score):
        slug = v["title"][:60]
        print(f"\n  [{i+1}/{len(to_score)}] {slug}...")

        results = {}

        # Gemini
        if gemini_client:
            gr = score_with_gemini(v, gemini_client)
            results.update(gr)
            if gr["gemini_score"] >= 0:
                print(f"    Gemini: {gr['gemini_score']} — {gr['gemini_reason'][:70]}")
            else:
                print(f"    Gemini: FAILED — {gr['gemini_reason'][:70]}")

        # Groq
        if groq_client:
            qr = score_with_groq(v, groq_client)
            results.update(qr)
            if qr["groq_score"] >= 0:
                print(f"    Groq:   {qr['groq_score']} — {qr['groq_reason'][:70]}")
            else:
                print(f"    Groq:   FAILED — {qr['groq_reason'][:70]}")

        scored_ids[v["id"]] = results

        # Rate limit delay (use the longer one)
        if i < len(to_score) - 1:
            delay = max(
                GEMINI_DELAY if gemini_client else 0,
                GROQ_DELAY if groq_client else 0,
            )
            time.sleep(delay)

    # Update vacancies file
    updated = []
    for v in vacancies:
        if v["id"] in scored_ids:
            v.update(scored_ids[v["id"]])
            # Clean up old llm_score field if present
            v.pop("llm_score", None)
            v.pop("llm_reason", None)
        updated.append(v)

    with open(VACANCIES_FILE, "w") as f:
        for v in updated:
            f.write(json.dumps(v) + "\n")

    # Summary
    if gemini_client:
        g_ok = sum(1 for r in scored_ids.values() if r.get("gemini_score", -1) >= 0)
        g_fail = sum(1 for r in scored_ids.values() if r.get("gemini_score", -1) < 0)
        print(f"\n✓ Gemini: {g_ok} scored, {g_fail} failed")
    if groq_client:
        q_ok = sum(1 for r in scored_ids.values() if r.get("groq_score", -1) >= 0)
        q_fail = sum(1 for r in scored_ids.values() if r.get("groq_score", -1) < 0)
        print(f"✓ Groq:   {q_ok} scored, {q_fail} failed")


if __name__ == "__main__":
    main()
