"""
EURAXESS source — scrapes PhD positions from the EU's researcher mobility portal.

Queries the EURAXESS search page filtered by relevant research fields and
the R1 (First Stage Researcher / PhD) profile. Parses search result cards
without visiting individual job pages.
"""

import re
import time
from datetime import datetime
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from sources import country_code

BASE_URL = "https://euraxess.ec.europa.eu/jobs/search"
CRAWL_DELAY = int(__import__("os").environ.get("EURAXESS_DELAY", "8"))
MAX_PAGES = int(__import__("os").environ.get("EURAXESS_PAGES", "3"))

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# Research fields relevant to the candidate's profile
RESEARCH_FIELDS = {
    65: "Communication sciences",
    70: "Journalism",
    71: "Media studies",
    75: "Science communication",
    78: "Computer science",
    87: "Informatics",
    92: "Criminology",
    251: "Information science",
    367: "Political sciences",
    368: "Governance",
    369: "Policy studies",
    371: "Public policy",
    372: "Science and society",
    388: "Sociology",
    393: "Social shaping of technology",
}

# R1 = First Stage Researcher (PhD candidates)
R1_PROFILE_ID = 447


def _build_search_url(page: int = 0) -> str:
    """Build a EURAXESS search URL with all relevant filters."""
    params = []
    for i, field_id in enumerate(RESEARCH_FIELDS.keys()):
        params.append(f"f[{i}]=job_research_field:{field_id}")
    # R1 filter occupies the next index
    params.append(f"f[{len(RESEARCH_FIELDS)}]=job_research_profile:{R1_PROFILE_ID}")
    params.append("sort[name]=created")
    params.append("sort[direction]=DESC")
    if page > 0:
        params.append(f"page={page}")
    return BASE_URL + "?" + "&".join(params)


def _extract_country(work_locations_text: str) -> str:
    """Extract country name from Work Locations text like 'Number of offers: 1, Germany, ...'"""
    if not work_locations_text:
        return ""
    parts = work_locations_text.split(",")
    if len(parts) >= 2:
        return parts[1].strip()
    return ""


def _parse_search_results(html: str) -> list[dict]:
    """Parse EURAXESS search result cards from HTML."""
    soup = BeautifulSoup(html, "lxml")
    results = []

    for article in soup.select("article.ecl-content-item"):
        try:
            # Title and URL
            title_link = article.select_one("h3.ecl-content-block__title a")
            if not title_link:
                continue
            title = title_link.get_text(strip=True)
            href = title_link.get("href", "")
            job_id_match = re.search(r"/jobs/(\d+)", href)
            if not job_id_match:
                continue
            job_id = f"euraxess_{job_id_match.group(1)}"
            url = f"https://euraxess.ec.europa.eu{href}"

            # Institution (first primary meta link)
            institution = ""
            meta_links = article.select("li.ecl-content-block__primary-meta-item a")
            if meta_links:
                institution = meta_links[0].get_text(strip=True)

            # Posted date
            posted = ""
            meta_items = article.select("li.ecl-content-block__primary-meta-item")
            for item in meta_items:
                text = item.get_text(strip=True)
                if text.startswith("Posted on:"):
                    posted = text.replace("Posted on:", "").strip()

            # Description excerpt
            desc_el = article.select_one("div.ecl-content-block__description p")
            description = desc_el.get_text(strip=True) if desc_el else ""

            # Work Locations → country
            locations_div = article.select_one("div.id-Work-Locations")
            country = ""
            if locations_div:
                loc_text = locations_div.get_text(" ", strip=True)
                country = _extract_country(loc_text)

            # Research field
            field_div = article.select_one("div.id-Research-Field")
            research_fields = ""
            if field_div:
                field_links = field_div.select("a")
                research_fields = "; ".join(a.get_text(strip=True) for a in field_links)

            # Researcher profile
            profile_div = article.select_one("div.id-Researcher-Profile")
            researcher_profile = ""
            if profile_div:
                profile_link = profile_div.select_one("a")
                if profile_link:
                    researcher_profile = profile_link.get_text(strip=True)

            # Deadline (ISO datetime when available)
            deadline = ""
            deadline_div = article.select_one("div.id-Application-Deadline")
            if deadline_div:
                time_el = deadline_div.select_one("time[datetime]")
                if time_el:
                    deadline = time_el.get("datetime", "")[:10]  # YYYY-MM-DD
                else:
                    deadline = deadline_div.get_text(strip=True).replace("Application Deadline:", "").strip()

            results.append({
                "id": job_id,
                "source": "euraxess",
                "type": "phd",
                "url": url,
                "title": title,
                "employer": institution,
                "country": country_code(country) if country else "",
                "country_name": country,
                "department": "",
                "location": country,
                "deadline": deadline,
                "days_remaining": "",
                "research_fields": research_fields,
                "description": description[:8000],
                "metadata": {"posted": posted, "researcher_profile": researcher_profile},
                "scraped_at": datetime.now().isoformat(),
            })

        except Exception as e:
            print(f"    ✗ Parse error: {e}")
            continue

    return results


def _get_total_results(html: str) -> int:
    """Extract total result count from search page."""
    match = re.search(r"Search results.*?(\d[\d,]+)", html, re.DOTALL)
    if match:
        return int(match.group(1).replace(",", ""))
    return 0


def scrape(seen_ids: set[str], max_new: int = 0) -> list[str]:
    """
    Scrape new PhD positions from EURAXESS.

    Returns list of vacancy dicts with standard schema.
    """
    print("\n── EURAXESS ──────────────────────────────────────────")
    vacancies = []
    total_seen = 0

    for page in range(MAX_PAGES):
        url = _build_search_url(page)
        print(f"  Page {page + 1}...")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"    ✗ Fetch error: {e}")
            break

        if page == 0:
            total = _get_total_results(resp.text)
            print(f"  {total} total results across filtered fields")

        results = _parse_search_results(resp.text)
        if not results:
            print("    No more results")
            break

        new_on_page = 0
        for v in results:
            if v["id"] in seen_ids:
                total_seen += 1
                continue
            vacancies.append(v)
            seen_ids.add(v["id"])
            new_on_page += 1

        print(f"    {new_on_page} new, {total_seen} already seen")

        # Stop early if we've seen most results on this page (means we're
        # into previously-scraped territory)
        if new_on_page == 0:
            print("    All results on this page already seen — stopping")
            break

        if max_new > 0 and len(vacancies) >= max_new:
            vacancies = vacancies[:max_new]
            break

        if page < MAX_PAGES - 1:
            time.sleep(CRAWL_DELAY)

    print(f"  ✓ {len(vacancies)} new vacancies from EURAXESS")
    return vacancies
