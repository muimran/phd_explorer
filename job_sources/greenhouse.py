"""Greenhouse-based sources — ProPublica, The Economist."""

import time
from datetime import datetime

import requests

API_BASE = "https://boards-api.greenhouse.io/v1/boards"
CRAWL_DELAY = 3

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Accept": "application/json",
}

BOARDS = {
    "propublica": {"employer": "ProPublica", "country": "US", "country_name": "United States"},
    "theeconomistgroup": {"employer": "The Economist", "country": "GB", "country_name": "United Kingdom"},
}


def _fetch_job_detail(board_id: str, job_id: int) -> str:
    try:
        resp = requests.get(f"{API_BASE}/{board_id}/jobs/{job_id}", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", "")
        if content:
            from bs4 import BeautifulSoup
            return BeautifulSoup(content, "lxml").get_text(" ", strip=True)[:8000]
    except Exception:
        pass
    return ""


def _location_to_country(location_name: str, default_country: str) -> tuple[str, str]:
    loc = location_name.lower()
    if "new york" in loc or "washington" in loc or "united states" in loc or "chicago" in loc or "los angeles" in loc:
        return "US", "United States"
    if "london" in loc or "united kingdom" in loc:
        return "GB", "United Kingdom"
    if "hong kong" in loc:
        return "HK", "Hong Kong"
    if "singapore" in loc:
        return "SG", "Singapore"
    return default_country, dict(BOARDS.get("propublica", {})).get("country_name", "")


def scrape_board(board_id: str, seen_ids: set[str], max_new: int = 0) -> list[dict]:
    info = BOARDS.get(board_id, {})
    employer = info.get("employer", board_id)
    default_country = info.get("country", "")
    default_country_name = info.get("country_name", "")

    print(f"\n── {employer} ──────────────────────────────────────")
    try:
        resp = requests.get(f"{API_BASE}/{board_id}/jobs", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  ✗ Fetch error: {e}")
        return []

    jobs_data = data.get("jobs", [])
    print(f"  {len(jobs_data)} total jobs listed")

    vacancies = []
    for job in jobs_data:
        gh_id = job.get("id", "")
        job_id = f"{board_id}_{gh_id}"
        if job_id in seen_ids:
            continue

        location = job.get("location", {}).get("name", "")
        country, country_name = _location_to_country(location, default_country)
        if not country_name:
            country_name = default_country_name

        departments = job.get("departments", [])
        dept = departments[0].get("name", "") if departments else ""

        vacancies.append({
            "id": job_id,
            "source": board_id,
            "url": job.get("absolute_url", ""),
            "title": job.get("title", ""),
            "employer": employer,
            "location": location,
            "country": country,
            "country_name": country_name,
            "department": dept,
            "contract_type": "",
            "description": "",
            "posted_date": (job.get("updated_at") or "")[:10],
            "scraped_at": datetime.now().isoformat(),
        })
        seen_ids.add(job_id)

    if max_new > 0:
        vacancies = vacancies[:max_new]

    if vacancies:
        print(f"  Fetching descriptions for {len(vacancies)} new jobs...")
        for i, v in enumerate(vacancies):
            gh_id = v["id"].split("_", 1)[1]
            desc = _fetch_job_detail(board_id, int(gh_id))
            v["description"] = desc or f"{v['title']}. {v['employer']}. {v['department']}"
            if i < len(vacancies) - 1:
                time.sleep(CRAWL_DELAY)

    print(f"  ✓ {len(vacancies)} new jobs from {employer}")
    return vacancies


def scrape(seen_ids: set[str], max_new: int = 0) -> list[dict]:
    all_jobs = []
    for board_id in BOARDS:
        jobs = scrape_board(board_id, seen_ids, max_new)
        all_jobs.extend(jobs)
    return all_jobs
