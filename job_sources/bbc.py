"""BBC — scrapes jobs from the careers.bbc.co.uk keyword RSS feeds."""

import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup

RSS_URL = "https://careers.bbc.co.uk/services/rss/job/"
CRAWL_DELAY = 5

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Accept": "application/rss+xml, application/xml",
}

SEARCH_KEYWORDS = ["data", "journalist", "reporter", "editor", "investigations"]

_LOCATION_COUNTRY = [
    (("london", "salford", "cardiff", "glasgow", "bristol", "belfast", "birmingham", "united kingdom"), "GB", "United Kingdom"),
    (("new york", "washington", "los angeles", "united states"), "US", "United States"),
    (("sydney", "australia"), "AU", "Australia"),
    (("singapore",), "SG", "Singapore"),
    (("nairobi", "kenya"), "KE", "Kenya"),
    (("delhi", "mumbai", "india"), "IN", "India"),
]


def _location_to_country(loc: str) -> tuple[str, str]:
    lower = loc.lower()
    for needles, cc, name in _LOCATION_COUNTRY:
        if any(n in lower for n in needles):
            return cc, name
    return "GB", "United Kingdom"


def _parse_feed(xml: str) -> list[dict]:
    soup = BeautifulSoup(xml, "xml")
    jobs = []
    for item in soup.find_all("item"):
        raw_title = item.title.get_text(strip=True) if item.title else ""
        link = item.link.get_text(strip=True) if item.link else ""
        m = re.search(r"/(\d+)/?(?:\?|$)", link)
        if not raw_title or not m:
            continue

        title, location = raw_title, ""
        tm = re.match(r"^(.*)\s+\(([^()]*)\)$", raw_title)
        if tm:
            title, location = tm.group(1).strip(), tm.group(2).strip()

        desc_html = item.description.get_text() if item.description else ""
        desc = BeautifulSoup(desc_html, "lxml").get_text(" ", strip=True)
        lm = re.search(r"LOCATION:\s*([^\n<]+?)(?:\s{2,}|$| Any offer| PURPOSE)", desc)
        if lm:
            location = lm.group(1).strip()
        dm = re.search(r"DEPARTMENT:\s*(.+?)\s+LOCATION:", desc)

        posted = ""
        if item.pubDate:
            try:
                posted = parsedate_to_datetime(item.pubDate.get_text(strip=True)).strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                pass

        jobs.append({
            "job_num": m.group(1),
            "title": title,
            "url": link,
            "location": location,
            "department": dm.group(1).strip() if dm else "",
            "description": desc[:8000],
            "posted_date": posted,
        })
    return jobs


def scrape(seen_ids: set[str], max_new: int = 0) -> list[dict]:
    print("\n── BBC ──────────────────────────────────────────────")

    all_raw = {}
    for i, kw in enumerate(SEARCH_KEYWORDS):
        params = {"locale": "en_GB", "keywords": f"({kw})"}
        try:
            resp = requests.get(RSS_URL, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            items = _parse_feed(resp.text)
            for it in items:
                all_raw[it["job_num"]] = it
            print(f"  '{kw}': {len(items)} results")
        except requests.RequestException as e:
            print(f"  ✗ '{kw}' error: {e}")
        if i < len(SEARCH_KEYWORDS) - 1:
            time.sleep(CRAWL_DELAY)

    print(f"  {len(all_raw)} unique jobs across searches")

    vacancies = []
    for job_num, it in all_raw.items():
        job_id = f"bbc_{job_num}"
        if job_id in seen_ids:
            continue
        cc, cn = _location_to_country(it["location"])
        vacancies.append({
            "id": job_id,
            "source": "bbc",
            "url": it["url"],
            "title": it["title"],
            "employer": "BBC",
            "location": it["location"],
            "country": cc,
            "country_name": cn,
            "department": it["department"],
            "contract_type": "",
            "description": it["description"] or f"{it['title']}. BBC.",
            "posted_date": it["posted_date"],
            "scraped_at": datetime.now().isoformat(),
        })
        seen_ids.add(job_id)

    if max_new > 0:
        vacancies = vacancies[:max_new]

    print(f"  ✓ {len(vacancies)} new jobs from BBC")
    return vacancies
