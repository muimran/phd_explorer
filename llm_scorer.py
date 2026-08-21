"""
LLM-based vacancy scoring using Groq (Llama 3.3 70B, free tier).

Runs AFTER the keyword scraper. Reads vacancies from data/vacancies.jsonl,
sends each relevant one to Groq for scoring, and stores the LLM score
alongside the keyword score.

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

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_DELAY = 3  # seconds between calls (free tier: 30 RPM)

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

CALIBRATION EXAMPLES (score 0 — these are NOT relevant despite passing keyword filters):
- "PhD Candidate in Mechanism-Informed World Models and Agents" → 0
- "PhD in Optimization of Sustainable Agri-Food Supply Chains under Climate Risk" → 0
- "PhD Candidate: DONOR-PROTECT" → 0
- "PhD in Computational Imaging for High-Throughput Applications" → 0
- "PhD in Multi-Aperture Ultrasound Imaging for Abdominal Aortic Aneurysm" → 0
- "MSCA-DN PhD position: brain large axial field of view PET/CT" → 0
- "PhD position on Restoring Communicative Agency: Human-centered AI smart glasses for people with speech impairments" → 0
Be aggressive about scoring 0 for positions in medical imaging, supply chain, robotics,
biomedical engineering, agricultural science, or clinical research — even if they mention
AI, data science, or Python. The candidate's "computational" focus is about media/society, not hardware/biology.

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
        print(f"    ✗ API error: {e}")
        return {"groq_score": -1, "groq_reason": f"API error: {e}"}


def main():
    parser = argparse.ArgumentParser(description="LLM scorer for PhD vacancies")
    parser.add_argument("--rescore", action="store_true", help="Re-score all vacancies")
    parser.add_argument("--max", type=int, default=0, help="Max vacancies to score")
    args = parser.parse_args()

    if not GROQ_API_KEY:
        print("GROQ_API_KEY not set — skipping LLM scoring.")
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

    # Init Groq
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        print("  ✓ Groq client ready")
    except Exception as e:
        print(f"  ✗ Groq init failed: {e}")
        return

    # Determine what needs scoring
    if args.rescore:
        to_score = relevant
        print(f"  Re-scoring all {len(to_score)}")
    else:
        to_score = [v for v in relevant if v.get("groq_score") is None or v.get("groq_score", 0) < 0]
        print(f"  {len(to_score)} need scoring")

    if args.max > 0:
        to_score = to_score[:args.max]
        print(f"  Limiting to {args.max}")

    if not to_score:
        print("Nothing to score.")
        return

    # Score each vacancy
    scored_ids = {}
    for i, v in enumerate(to_score):
        slug = v["title"][:60]
        print(f"  [{i+1}/{len(to_score)}] {slug}...")

        result = score_with_groq(v, client)
        scored_ids[v["id"]] = result

        if result["groq_score"] >= 0:
            print(f"    → {result['groq_score']} — {result['groq_reason'][:70]}")
        else:
            print(f"    → FAILED — {result['groq_reason'][:70]}")

        if i < len(to_score) - 1:
            time.sleep(GROQ_DELAY)

    # Update vacancies file
    updated = []
    for v in vacancies:
        if v["id"] in scored_ids:
            v.update(scored_ids[v["id"]])
            # Clean up old fields
            v.pop("llm_score", None)
            v.pop("llm_reason", None)
            v.pop("gemini_score", None)
            v.pop("gemini_reason", None)
        updated.append(v)

    with open(VACANCIES_FILE, "w") as f:
        for v in updated:
            f.write(json.dumps(v) + "\n")

    ok = sum(1 for r in scored_ids.values() if r["groq_score"] >= 0)
    fail = sum(1 for r in scored_ids.values() if r["groq_score"] < 0)
    print(f"\n✓ LLM scoring complete: {ok} scored, {fail} failed")


if __name__ == "__main__":
    main()
