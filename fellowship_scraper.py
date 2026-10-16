"""
Fellowship Scraper — curated recurring programmes plus live calls from IJNet.

Usage:
    python fellowship_scraper.py
"""

import json
import re
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent / "data"
CURATED_FILE = DATA_DIR / "fellowships_curated.json"
FELLOWSHIPS_FILE = DATA_DIR / "fellowships.jsonl"

IJNET_GRAPHQL = "https://ijnet.org/graphql"
IJNET_PAGES = 3

FELLOWSHIP_SOURCE_META = {
    "curated": {"label": "Programme", "color": "#7c3aed"},
    "ijnet": {"label": "IJNet", "color": "#0e7490"},
}

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Content-Type": "application/json",
}

_QUERY = """query opportunitiesView {
  opportunitiesView(page: %d, offset: 0, filter: {}, pageSize: 50) {
    results { ... on NodeOpportunity {
      title
      deadline: fieldDeadline { date }
      url: entityUrl { path }
      body: fieldSummary { summary: processed }
      regions: fieldRegion { entity { ... on TaxonomyTermOpportunityRegion { label: entityLabel } } }
    } }
  }
}"""

AI_GOV = ["ai governance", "ai policy", "artificial intelligence", " ai ", "ai-", "algorithm", "tech policy", "technology policy"]
DATA = ["data", "investigat", "fact-check", "disinformation", "misinformation", "osint", "open source", "digital", "technology", "tech "]
RESEARCH = ["research", "academic", "phd", "doctoral", "university"]
FELLOWSHIP_WORDS = ["fellowship", "fellow", "residency"]
LOW_VALUE = ["webinar", "workshop", "conference", "award", "prize", "contest", "competition", "festival"]
REGIONAL = re.compile(r"\b(?:for|from) (?:[A-Z][a-z]+ ){1,3}(?:journalists|reporters|media)\b|journalists (?:from|based in|in) [A-Z]")
# IJNet calls for programmes already in the curated list
CURATED_ALIASES = ["stanford", "nieman", "reuters institute", "tarbell", "knight-wallace", "knight science", "pulitzer center", "berkman"]

REGION_CODES = {"Worldwide": "Global", "Europe": "Europe", "US and Canada": "US", "United Kingdom": "GB"}


def _label(text: str) -> str:
    if any(k in text for k in AI_GOV):
        return "AI governance" if ("policy" in text or "governance" in text) else "AI journalism"
    if any(k in text for k in RESEARCH):
        return "Research"
    return "Journalism"


def _score_ijnet(title: str, summary: str) -> int:
    text = f" {title} {summary} ".lower()
    score = 0
    if any(k in text for k in FELLOWSHIP_WORDS):
        score += 30
    if any(k in text for k in AI_GOV):
        score += 35
    if any(k in text for k in DATA):
        score += 20
    if any(k in text for k in RESEARCH):
        score += 10
    if any(k in text for k in LOW_VALUE):
        score -= 20
    # Calls restricted to one nationality rarely fit
    if REGIONAL.search(f"{title} {summary}"):
        score -= 25
    return max(0, min(score, 100))


def scrape_ijnet() -> list[dict]:
    print("\n── IJNet ────────────────────────────────────────────")
    today = date.today().isoformat()
    out = []
    for page in range(IJNET_PAGES):
        try:
            resp = requests.post(IJNET_GRAPHQL, json={"query": _QUERY % page}, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            results = resp.json()["data"]["opportunitiesView"]["results"]
        except (requests.RequestException, KeyError, TypeError, ValueError) as e:
            print(f"  ✗ Fetch error: {e}")
            break
        # Results are sorted by deadline, newest first: stop once they're all past
        open_items = [r for r in results if r and ((r.get("deadline") or {}).get("date") or "")[:10] >= today]
        for r in open_items:
            title = (r.get("title") or "").strip()
            summary = BeautifulSoup((r.get("body") or {}).get("summary") or "", "lxml").get_text(" ", strip=True)
            if any(a in f"{title} {summary}".lower() for a in CURATED_ALIASES):
                continue
            path = (r.get("url") or {}).get("path", "")
            regions = [((x or {}).get("entity") or {}).get("label", "") for x in (r.get("regions") or [])]
            region = regions[0] if regions else "Global"
            score = _score_ijnet(title, summary)
            out.append({
                "id": f"ijnet_{path.rsplit('/', 1)[-1]}",
                "source": "ijnet",
                "url": f"https://ijnet.org{path}",
                "title": title,
                "employer": summary[:160],
                "country": REGION_CODES.get(region, region),
                "country_name": region,
                "deadline": r["deadline"]["date"][:10],
                "label": _label(f" {title} {summary} ".lower()),
                "note": "",
                "description": summary,
                "score": score,
                "recommendation": "INVESTIGATE" if score >= 15 else "IGNORE",
                "scraped_at": datetime.now().isoformat(),
            })
        if len(open_items) < len(results):
            break
    print(f"  ✓ {len(out)} open opportunities")
    return out


def load_curated() -> list[dict]:
    items = json.loads(CURATED_FILE.read_text())
    for it in items:
        it.setdefault("source", "curated")
        it.setdefault("description", it.get("note", ""))
        it.setdefault("country_name", it.get("country", ""))
        it["recommendation"] = "INVESTIGATE"
        it.setdefault("scraped_at", datetime.now().isoformat())
    return items


def load_fellowships() -> list[dict]:
    items = load_curated()
    if FELLOWSHIPS_FILE.exists():
        items += [json.loads(l) for l in FELLOWSHIPS_FILE.read_text().splitlines() if l.strip()]
    return [i for i in items if i.get("recommendation") != "IGNORE"]


def main():
    live = scrape_ijnet()
    with FELLOWSHIPS_FILE.open("w") as f:
        for it in live:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    kept = [i for i in live if i["recommendation"] != "IGNORE"]
    print(f"✓ {len(kept)} relevant IJNet calls + {len(load_curated())} curated programmes")


if __name__ == "__main__":
    main()
