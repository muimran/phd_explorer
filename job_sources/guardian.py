"""The Guardian — scrapes jobs from workwithus.theguardian.com JSON API."""

import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

API_URL = "https://workwithus.theguardian.com/_33/jobs_json"
BASE_URL = "https://workwithus.theguardian.com"
CRAWL_DELAY = 5

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Accept": "application/json, text/html",
}


def _fetch_description(jd_path: str) -> str:
    try:
        resp = requests.get(f"{BASE_URL}{jd_path}", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        main = soup.select_one(".job-description") or soup.select_one("main") or soup.select_one("article")
        if main:
            return main.get_text(" ", strip=True)[:8000]
    except requests.RequestException:
        pass
    return ""


def _country_code(country: str) -> str:
    mapping = {
        "United Kingdom": "GB",
        "United States": "US",
        "Australia": "AU",
    }
    return mapping.get(country, country[:2].upper() if country else "")


def scrape(seen_ids: set[str], max_new: int = 0) -> list[dict]:
    print("\n── The Guardian ──────────────────────────────────────")
    try:
        resp = requests.get(API_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  ✗ Fetch error: {e}")
        return []

    jobs_data = data.get("jobs", [])
    print(f"  {len(jobs_data)} total jobs listed")

    vacancies = []
    for job in jobs_data:
        job_id = f"guardian_{job.get('requisition_id', '')}"
        if job_id in seen_ids:
            continue

        country = job.get("country", "")
        vacancies.append({
            "id": job_id,
            "source": "guardian",
            "url": f"{BASE_URL}{job.get('jd_url', '')}",
            "title": job.get("title", ""),
            "employer": "The Guardian",
            "location": job.get("location", ""),
            "country": _country_code(country),
            "country_name": country,
            "department": job.get("business_unit", ""),
            "contract_type": job.get("contract_type", ""),
            "description": "",
            "posted_date": "",
            "scraped_at": datetime.now().isoformat(),
        })
        seen_ids.add(job_id)

    if max_new > 0:
        vacancies = vacancies[:max_new]

    if vacancies:
        print(f"  Fetching descriptions for {len(vacancies)} new jobs...")
        for i, v in enumerate(vacancies):
            path = v["url"].replace(BASE_URL, "")
            desc = _fetch_description(path)
            v["description"] = desc or f"{v['title']}. {v['employer']}. {v['department']}"
            if i < len(vacancies) - 1:
                time.sleep(CRAWL_DELAY)

    print(f"  ✓ {len(vacancies)} new jobs from The Guardian")
    return vacancies
