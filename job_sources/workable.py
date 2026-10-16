"""Workable-based sources — Bellingcat."""

from datetime import datetime

import requests
from bs4 import BeautifulSoup

from sources import country_code

API = "https://apply.workable.com/api/v1/widget/accounts/{account}?details=true"

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Accept": "application/json",
}

ACCOUNTS = {
    "bellingcat": {"employer": "Bellingcat", "country": "NL", "country_name": "Netherlands"},
}


def scrape_account(account: str, seen_ids: set[str], max_new: int = 0) -> list[dict]:
    info = ACCOUNTS[account]
    print(f"\n── {info['employer']} ──────────────────────────────────────")
    try:
        resp = requests.get(API.format(account=account), headers=HEADERS, timeout=30)
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
    except (requests.RequestException, ValueError) as e:
        print(f"  ✗ Fetch error: {e}")
        return []
    print(f"  {len(jobs)} total jobs listed")

    vacancies = []
    for job in jobs:
        job_id = f"{account}_{job.get('shortcode', '')}"
        if job_id in seen_ids or not job.get("title"):
            continue
        country_name = job.get("country") or info["country_name"]
        city = job.get("city") or ""
        desc = BeautifulSoup(job.get("description") or "", "lxml").get_text(" ", strip=True)
        vacancies.append({
            "id": job_id,
            "source": account,
            "url": job.get("url") or f"https://apply.workable.com/{account}/j/{job.get('shortcode', '')}/",
            "title": job["title"],
            "employer": info["employer"],
            "location": ", ".join(p for p in (city, country_name) if p),
            "country": country_code(country_name) if job.get("country") else info["country"],
            "country_name": country_name,
            "department": job.get("department") or "",
            "contract_type": job.get("employment_type") or "",
            "description": desc[:8000] or f"{job['title']}. {info['employer']}.",
            "posted_date": (job.get("published_on") or "")[:10],
            "scraped_at": datetime.now().isoformat(),
        })
        seen_ids.add(job_id)

    if max_new > 0:
        vacancies = vacancies[:max_new]
    print(f"  ✓ {len(vacancies)} new jobs from {info['employer']}")
    return vacancies


def scrape(seen_ids: set[str], max_new: int = 0) -> list[dict]:
    all_jobs = []
    for account in ACCOUNTS:
        all_jobs.extend(scrape_account(account, seen_ids, max_new))
    return all_jobs
