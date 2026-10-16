"""New York Times — scrapes jobs from nytco.com/careers/job-listings/."""

import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

LIST_URL = "https://www.nytco.com/careers/job-listings/"
BASE_URL = "https://www.nytco.com"
CRAWL_DELAY = 5

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Accept": "text/html",
}


def _fetch_description(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        content = soup.select_one(".job-post-content") or soup.select_one("article") or soup.select_one(".entry-content")
        if content:
            return content.get_text(" ", strip=True)[:8000]
    except requests.RequestException:
        pass
    return ""


def _parse_location(location_text: str) -> tuple[str, str, str]:
    loc = location_text.strip()
    lower = loc.lower()
    if "new york" in lower or "washington" in lower or "los angeles" in lower or "san francisco" in lower:
        return loc, "US", "United States"
    if "london" in lower:
        return loc, "GB", "United Kingdom"
    if not loc:
        return "New York, NY", "US", "United States"
    return loc, "US", "United States"


def scrape(seen_ids: set[str], max_new: int = 0) -> list[dict]:
    print("\n── New York Times ────────────────────────────────────")
    try:
        resp = requests.get(LIST_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ✗ Fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    links = soup.select('a[href*="/careers/job-listings/"]')

    jobs_raw = []
    seen_hrefs = set()
    for link in links:
        href = link.get("href", "")
        if href in ("/careers/job-listings/", "") or href in seen_hrefs:
            continue
        if not re.search(r'/careers/job-listings/\d+-', href):
            continue
        seen_hrefs.add(href)
        title_el = link.select_one("h2")
        title = (title_el or link).get_text(strip=True)
        if not title:
            continue
        loc_el = link.select_one("span")
        match = re.search(r'/(\d+)-', href)
        req_id = match.group(1) if match else ""
        jobs_raw.append({
            "title": title, "href": href, "req_id": req_id,
            "location": loc_el.get_text(strip=True) if loc_el else "",
        })

    print(f"  {len(jobs_raw)} total jobs listed")

    vacancies = []
    for job in jobs_raw:
        job_id = f"nyt_{job['req_id']}"
        if job_id in seen_ids:
            continue

        url = f"{BASE_URL}{job['href']}" if job["href"].startswith("/") else job["href"]
        location, country, country_name = _parse_location(job["location"])

        vacancies.append({
            "id": job_id,
            "source": "nytimes",
            "url": url,
            "title": job["title"],
            "employer": "The New York Times",
            "location": location,
            "country": country,
            "country_name": country_name,
            "department": "",
            "contract_type": "",
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
            desc = _fetch_description(v["url"])
            v["description"] = desc or f"{v['title']}. {v['employer']}"
            if i < len(vacancies) - 1:
                time.sleep(CRAWL_DELAY)

    print(f"  ✓ {len(vacancies)} new jobs from The New York Times")
    return vacancies
