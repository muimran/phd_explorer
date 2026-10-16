"""
Job Scraper — scrapes data journalism & media roles from major newsrooms.

Scrapes jobs from multiple news organizations, scores them for relevance,
and stores results for the site generator.

Usage:
    python job_scraper.py                  # scrape + score
    python job_scraper.py --max 10         # limit per source
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from profile import (
    JOURNALISM_MEDIA, TECH_DATA, JOURNALISM_ADJACENT, YOUR_SKILLS,
    REGEX_SKILLS, WEIGHTS, PROFILE_SUMMARY,
)
from job_sources import guardian, greenhouse, workday, nytimes, bbc

DATA_DIR = Path(__file__).parent / "data"
JOBS_FILE = DATA_DIR / "jobs.jsonl"
JOBS_SEEN_FILE = DATA_DIR / "jobs_seen_ids.json"

JOB_SOURCES = [guardian, greenhouse, workday, nytimes, bbc]

JOB_SOURCE_META = {
    "guardian": {"label": "The Guardian", "color": "#052962"},
    "propublica": {"label": "ProPublica", "color": "#000000"},
    "theeconomistgroup": {"label": "The Economist", "color": "#e3120b"},
    "washpost": {"label": "Washington Post", "color": "#231f20"},
    "reuters": {"label": "Reuters", "color": "#ff8000"},
    "nytimes": {"label": "NYT", "color": "#1a1a1a"},
    "bbc": {"label": "BBC", "color": "#bb1919"},
}


def load_seen() -> set[str]:
    if JOBS_SEEN_FILE.exists():
        return set(json.loads(JOBS_SEEN_FILE.read_text()))
    return set()


def save_seen(ids: set[str]):
    JOBS_SEEN_FILE.write_text(json.dumps(sorted(ids)))


def load_jobs() -> list[dict]:
    if not JOBS_FILE.exists():
        return []
    jobs = []
    for line in JOBS_FILE.read_text().splitlines():
        if line.strip():
            jobs.append(json.loads(line))
    return jobs


def save_jobs(jobs: list[dict]):
    with JOBS_FILE.open("w") as f:
        for j in jobs:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")


# Keywords that indicate a data/journalism/media role we care about
DATA_JOURNALISM_KEYWORDS = [
    "data journalist", "data reporter", "data editor",
    "data visualization", "data viz", "visual journalist",
    "investigative reporter", "investigative journalist",
    "graphics reporter", "graphics editor", "news graphics",
    "computational journalist", "interactive",
    "data analyst", "data science", "data scientist",
    "audience", "digital journalist", "multimedia journalist",
    "reporter", "correspondent", "editor",
    "journalist", "producer", "researcher",
]

# Roles we definitely don't want
EXCLUDE_ROLES = [
    "sales", "accounting", "accountant", "payroll", "hr ",
    "human resources", "facilities", "security guard",
    "driver", "delivery", "warehouse", "cleaner",
    "receptionist", "catering", "tax", "finance", "fp&a",
    "pitch form", "admin", "legal editor", "compensation",
    "advertising", "commerce", "marketing",
]


def is_relevant_job(job: dict) -> bool:
    title = job["title"].lower()
    if any(ex in title for ex in EXCLUDE_ROLES):
        return False
    return True


# Title signals: the role itself must be data- or journalism-shaped
TITLE_DATA = [
    "data", "visual", "graphics", "investigat", "interactive", "computational",
    "verify", "fact-check", "fact check", "osint", "open source", "insights",
    "analytics", "ai ", "a.i.", "artificial intelligence",
]
TITLE_JOURNALISM = [
    "journalis", "reporter", "correspondent", "editor", "producer",
    "newsroom", "news ", "fellow",
]
# Description signals: tools and methods a data journalist uses
DESC_SKILLS = [
    "python", " sql", " r ", "datawrapper", "d3", "scraping", "statistic",
    "spreadsheet", "data analysis", "data visuali", "open-source intelligence",
    "foia", "public records", "investigative", "data journalism", "machine learning",
]


def score_job(job: dict) -> dict:
    title = " " + job["title"].lower() + " "
    desc = " " + job.get("description", "").lower() + " "

    data_in_title = any(k in title for k in TITLE_DATA)
    journo_in_title = any(k in title for k in TITLE_JOURNALISM)

    score = 0
    if data_in_title and journo_in_title:
        score += 70
    elif data_in_title:
        score += 40
    elif journo_in_title:
        score += 30

    skill_hits = sum(1 for k in DESC_SKILLS if k in desc)
    score += min(skill_hits * 4, 30)

    if not (data_in_title or journo_in_title):
        score = min(score, 10)
    elif not data_in_title:
        # Strong (>= 40) is reserved for roles with a data signal in the title
        score = min(score, 39)

    score = min(score, 100)

    if score >= 40:
        rec = "STRONG"
    elif score >= 15:
        rec = "INVESTIGATE"
    elif score >= 5:
        rec = "WEAK"
    else:
        rec = "IGNORE"

    return {**job, "score": score, "recommendation": rec}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=0)
    parser.add_argument("--rescore", action="store_true", help="Rescore stored jobs without scraping")
    args = parser.parse_args()

    if args.rescore:
        jobs = [score_job(j) for j in load_jobs() if is_relevant_job(j)]
        jobs = [j for j in jobs if j["recommendation"] != "IGNORE"]
        save_jobs(jobs)
        print(f"✓ Rescored → {len(jobs)} jobs kept")
        return

    seen_ids = load_seen()
    existing_jobs = load_jobs()
    new_jobs = []

    for source in JOB_SOURCES:
        try:
            jobs = source.scrape(seen_ids, args.max)
            new_jobs.extend(jobs)
        except Exception as e:
            print(f"  ✗ Source error: {e}")

    # Filter and score
    relevant = []
    excluded = 0
    for job in new_jobs:
        if not is_relevant_job(job):
            excluded += 1
            continue
        scored = score_job(job)
        if scored["recommendation"] != "IGNORE":
            relevant.append(scored)

    print(f"\n✓ Scraped {len(new_jobs)} new jobs → {len(relevant)} relevant (excluded {excluded})")

    all_jobs = existing_jobs + relevant
    save_jobs(all_jobs)
    save_seen(seen_ids)
    print(f"  Total jobs in database: {len(all_jobs)}")


if __name__ == "__main__":
    main()
