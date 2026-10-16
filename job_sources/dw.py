"""Deutsche Welle — scrapes English-language postings from jobs.dw.com (Umantis)."""

import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

LIST_URL = "https://jobs.dw.com/Jobs/All?lang=eng"
BASE_URL = "https://jobs.dw.com"
CRAWL_DELAY = 5
# Umantis suffixes each posting with its language: /Description/2 is English, /1 German
ENGLISH = "2"

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Accept": "text/html",
}


def _fetch_description(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        main = soup.select_one("main") or soup.select_one("#content") or soup.body
        return main.get_text(" ", strip=True)[:8000] if main else ""
    except requests.RequestException:
        return ""


def scrape(seen_ids: set[str], max_new: int = 0) -> list[dict]:
    print("\n── Deutsche Welle ────────────────────────────────────")
    try:
        resp = requests.get(LIST_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ✗ Fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    raw = {}
    for a in soup.select('a[href*="/Description/"]'):
        m = re.search(r"/Vacancies/(\d+)/Description/(\d)", a["href"])
        if not m or m.group(2) != ENGLISH:
            continue
        row = a.find_parent(["tr", "li", "div"])
        row_text = row.get_text(" | ", strip=True) if row else ""
        dept = re.search(r"Department:\s*([^|]+)", row_text)
        loc = re.search(r"\|\s*([A-Z][\w ]+(?: or [A-Z][\w ]+)?)\s*\|\s*[A-Z][^|]{10,}\|\s*Apply", row_text)
        raw[m.group(1)] = {
            "title": a.get_text(strip=True),
            "href": a["href"],
            "department": dept.group(1).strip() if dept else "",
            "location": loc.group(1).strip() if loc else "Bonn / Berlin",
        }
    print(f"  {len(raw)} English-language postings")

    vacancies = []
    for vac_id, r in raw.items():
        job_id = f"dw_{vac_id}"
        if job_id in seen_ids:
            continue
        vacancies.append({
            "id": job_id,
            "source": "dw",
            "url": f"{BASE_URL}{r['href']}",
            "title": r["title"],
            "employer": "Deutsche Welle",
            "location": r["location"],
            "country": "DE",
            "country_name": "Germany",
            "department": r["department"],
            "contract_type": "",
            "description": "",
            "posted_date": "",
            "scraped_at": datetime.now().isoformat(),
        })
        seen_ids.add(job_id)

    if max_new > 0:
        vacancies = vacancies[:max_new]

    for i, v in enumerate(vacancies):
        v["description"] = _fetch_description(v["url"]) or f"{v['title']}. Deutsche Welle."
        if i < len(vacancies) - 1:
            time.sleep(CRAWL_DELAY)

    print(f"  ✓ {len(vacancies)} new jobs from Deutsche Welle")
    return vacancies
