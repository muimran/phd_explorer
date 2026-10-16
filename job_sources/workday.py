"""Workday-based sources — Washington Post, Reuters."""

import time
from datetime import datetime

import requests

CRAWL_DELAY = 5
MAX_RESULTS = 100

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

SITES = {
    "washpost": {
        "employer": "Washington Post",
        "tenant": "washpost",
        "site": "washingtonpostcareers",
        "host": "washpost.wd5.myworkdayjobs.com",
        "default_country": "US",
        "default_country_name": "United States",
    },
    "reuters": {
        "employer": "Reuters / Thomson Reuters",
        "tenant": "thomsonreuters",
        "site": "External_Career_Site",
        "host": "thomsonreuters.wd5.myworkdayjobs.com",
        "default_country": "US",
        "default_country_name": "United States",
        "search_terms": ["journalist", "reporter", "correspondent", "data journalism"],
    },
}


def _location_to_country(loc_text: str) -> tuple[str, str]:
    loc = loc_text.lower()
    if "united kingdom" in loc or "london" in loc:
        return "GB", "United Kingdom"
    if "united states" in loc or "new york" in loc or "washington" in loc or "san francisco" in loc:
        return "US", "United States"
    if "canada" in loc or "toronto" in loc:
        return "CA", "Canada"
    if "australia" in loc or "sydney" in loc:
        return "AU", "Australia"
    if "singapore" in loc:
        return "SG", "Singapore"
    return "", loc_text


def scrape_site(site_key: str, seen_ids: set[str], max_new: int = 0) -> list[dict]:
    info = SITES[site_key]
    employer = info["employer"]
    host = info["host"]
    tenant = info["tenant"]
    site = info["site"]
    # Workday ANDs multi-word queries, so each term is a separate search
    search_terms = info.get("search_terms", [""])

    print(f"\n── {employer} ──────────────────────────────────────")

    api_url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    by_path = {}
    for term in search_terms:
        offset = 0
        total = 0
        # Workday rejects limit > 20
        while offset < MAX_RESULTS:
            body = {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term}
            try:
                resp = requests.post(api_url, json=body, headers=HEADERS, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                print(f"  ✗ Fetch error ('{term}'): {e}")
                break
            if offset == 0:
                total = data.get("total", 0)
            page = data.get("jobPostings", [])
            for p in page:
                by_path[p.get("externalPath", "")] = p
            offset += 20
            if not page or offset >= total:
                break
            time.sleep(CRAWL_DELAY)
        if term:
            print(f"  '{term}': {total} results")
        time.sleep(CRAWL_DELAY)

    job_postings = [p for path, p in by_path.items() if path]
    print(f"  {len(job_postings)} unique jobs fetched")

    vacancies = []
    for job in job_postings:
        ext_path = job.get("externalPath", "")
        job_id = f"{site_key}_{ext_path.split('/')[-1]}" if ext_path else ""
        if not job_id or job_id in seen_ids:
            continue

        location = job.get("locationsText", "")
        country, country_name = _location_to_country(location)
        if not country:
            country = info["default_country"]
            country_name = info["default_country_name"]

        vacancies.append({
            "id": job_id,
            "source": site_key,
            "url": f"https://{host}/en-US{ext_path}",
            "title": job.get("title", ""),
            "employer": employer,
            "location": location,
            "country": country,
            "country_name": country_name,
            "department": "",
            "contract_type": "",
            "description": job.get("title", ""),
            "posted_date": (job.get("postedOn") or "")[:10],
            "scraped_at": datetime.now().isoformat(),
        })
        seen_ids.add(job_id)

    if max_new > 0:
        vacancies = vacancies[:max_new]

    print(f"  ✓ {len(vacancies)} new jobs from {employer}")
    return vacancies


def scrape(seen_ids: set[str], max_new: int = 0) -> list[dict]:
    all_jobs = []
    for site_key in SITES:
        jobs = scrape_site(site_key, seen_ids, max_new)
        all_jobs.extend(jobs)
        if site_key != list(SITES.keys())[-1]:
            time.sleep(CRAWL_DELAY)
    return all_jobs
