"""
jobs.ac.uk source — scrapes PhD positions from the UK's leading academic jobs site.

Queries the jobs.ac.uk search page filtered by relevant academic disciplines
and PhD job type. Parses search result cards from the listing pages.
"""

import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.jobs.ac.uk/search/"
CRAWL_DELAY = int(__import__("os").environ.get("JOBSACUK_DELAY", "8"))
MAX_PAGES = int(__import__("os").environ.get("JOBSACUK_PAGES", "3"))

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

DISCIPLINE_FACETS = [
    "media-communication-cultural-studies",
    "computer-sciences",
    "politics-international-studies",
    "sociology",
    "journalism",
    "information-services",
    "law",
]


def _build_search_url(start_index: int = 1) -> str:
    """Build a jobs.ac.uk search URL with relevant filters."""
    facets = "&".join(
        f"activeFacets=academicDisciplineFacet%3A{f}" for f in DISCIPLINE_FACETS
    )
    return (
        f"{BASE_URL}?keywords=funded+PhD&{facets}"
        f"&jobType=phds&sortOrder=1&pageSize=25&startIndex={start_index}"
    )


def _parse_deadline(text: str) -> str:
    """Parse deadline text like '15 Jan' or '25 Oct 2026' to ISO format."""
    text = text.strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # No year — assume next occurrence
    for fmt in ("%d %b", "%d %B"):
        try:
            d = datetime.strptime(text, fmt)
            now = datetime.now()
            d = d.replace(year=now.year)
            if d < now:
                d = d.replace(year=now.year + 1)
            return d.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


def _parse_search_results(html: str) -> list[dict]:
    """Parse jobs.ac.uk search result cards from HTML."""
    soup = BeautifulSoup(html, "lxml")
    results = []

    for card in soup.select("div.j-search-result__result"):
        try:
            job_id = card.get("data-advert-id", "")
            if not job_id:
                continue

            title_link = card.select_one("a[href*='/job/']")
            if not title_link:
                continue
            title = title_link.get_text(strip=True)
            href = title_link.get("href", "")
            url = f"https://www.jobs.ac.uk{href}" if href.startswith("/") else href

            employer_el = card.select_one(".j-search-result__employer")
            employer = employer_el.get_text(strip=True) if employer_el else ""

            dept_el = card.select_one(".j-search-result__department")
            department = dept_el.get_text(strip=True) if dept_el else ""

            location = ""
            for div in card.select("div"):
                text = div.get_text(strip=True)
                if text and div.get("class") is None and not div.find("a"):
                    if employer and text != employer and "PhD" not in text and len(text) < 100:
                        location = text
                        break

            salary_el = card.select_one(".j-search-result__info")
            salary = salary_el.get_text(strip=True) if salary_el else ""

            deadline = ""
            deadline_el = card.select_one(".j-search-result__date--blue")
            if deadline_el:
                deadline = _parse_deadline(deadline_el.get_text(strip=True))

            results.append({
                "id": f"jobsacuk_{job_id}",
                "source": "jobsacuk",
                "type": "phd",
                "url": url,
                "title": title,
                "employer": employer,
                "country": "GB",
                "country_name": "United Kingdom",
                "department": department,
                "location": location,
                "deadline": deadline,
                "days_remaining": "",
                "research_fields": "",
                "description": f"{title}. {employer}. {department}. {salary}",
                "metadata": {"salary": salary},
                "scraped_at": datetime.now().isoformat(),
            })

        except Exception as e:
            print(f"    ✗ Parse error: {e}")
            continue

    return results


def _get_total_results(html: str) -> int:
    """Extract total result count from search page."""
    soup = BeautifulSoup(html, "lxml")
    count_el = soup.select_one("span.job-count")
    if count_el:
        match = re.search(r"(\d[\d,]*)", count_el.get_text())
        if match:
            return int(match.group(1).replace(",", ""))
    count_el = soup.select_one(".j-search-content__count")
    if count_el:
        match = re.search(r"(\d[\d,]*)", count_el.get_text())
        if match:
            return int(match.group(1).replace(",", ""))
    return 0


def scrape(seen_ids: set[str], max_new: int = 0) -> list[dict]:
    """
    Scrape new PhD positions from jobs.ac.uk.

    Returns list of vacancy dicts with standard schema.
    """
    print("\n── jobs.ac.uk ────────────────────────────────────────")
    vacancies = []
    total_seen = 0

    for page in range(MAX_PAGES):
        start_index = page * 25 + 1
        url = _build_search_url(start_index)
        print(f"  Page {page + 1} (startIndex={start_index})...")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"    ✗ Fetch error: {e}")
            break

        if page == 0:
            total = _get_total_results(resp.text)
            print(f"  {total} total results across filtered disciplines")

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

        if new_on_page == 0:
            print("    All results on this page already seen — stopping")
            break

        if max_new > 0 and len(vacancies) >= max_new:
            vacancies = vacancies[:max_new]
            break

        if page < MAX_PAGES - 1:
            time.sleep(CRAWL_DELAY)

    print(f"  ✓ {len(vacancies)} new vacancies from jobs.ac.uk")
    return vacancies
