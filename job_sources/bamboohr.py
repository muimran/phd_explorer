"""BambooHR-based sources — OCCRP."""

import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from sources import country_code

CRAWL_DELAY = 3

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Accept": "application/json",
}

COMPANIES = {
    "occrp": {"employer": "OCCRP"},
}


def _detail(company: str, job_id: str) -> dict:
    try:
        resp = requests.get(f"https://{company}.bamboohr.com/careers/{job_id}/detail", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json().get("result", {}).get("jobOpening", {})
    except (requests.RequestException, ValueError):
        return {}


def scrape_company(company: str, seen_ids: set[str], max_new: int = 0) -> list[dict]:
    employer = COMPANIES[company]["employer"]
    print(f"\n── {employer} ──────────────────────────────────────")
    try:
        resp = requests.get(f"https://{company}.bamboohr.com/careers/list", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        listing = resp.json().get("result", [])
    except (requests.RequestException, ValueError) as e:
        print(f"  ✗ Fetch error: {e}")
        return []
    print(f"  {len(listing)} total jobs listed")

    vacancies = []
    for job in listing:
        job_id = f"{company}_{job.get('id', '')}"
        if job_id in seen_ids:
            continue
        loc = job.get("atsLocation") or {}
        country_name = loc.get("country") or ""
        city = loc.get("city") or ""
        vacancies.append({
            "id": job_id,
            "source": company,
            "url": f"https://{company}.bamboohr.com/careers/{job.get('id', '')}",
            "title": job.get("jobOpeningName", ""),
            "employer": employer,
            "location": ", ".join(p for p in (city, country_name) if p) or "Remote",
            "country": country_code(country_name) if country_name else "",
            "country_name": country_name,
            "department": job.get("departmentLabel") or "",
            "contract_type": job.get("employmentStatusLabel") or "",
            "description": "",
            "posted_date": "",
            "scraped_at": datetime.now().isoformat(),
        })
        seen_ids.add(job_id)

    if max_new > 0:
        vacancies = vacancies[:max_new]

    for i, v in enumerate(vacancies):
        d = _detail(company, v["id"].split("_", 1)[1])
        desc = BeautifulSoup(d.get("description") or "", "lxml").get_text(" ", strip=True)
        v["description"] = desc[:8000] or f"{v['title']}. {employer}."
        v["posted_date"] = d.get("datePosted") or ""
        if i < len(vacancies) - 1:
            time.sleep(CRAWL_DELAY)

    print(f"  ✓ {len(vacancies)} new jobs from {employer}")
    return vacancies


def scrape(seen_ids: set[str], max_new: int = 0) -> list[dict]:
    all_jobs = []
    for company in COMPANIES:
        all_jobs.extend(scrape_company(company, seen_ids, max_new))
    return all_jobs
