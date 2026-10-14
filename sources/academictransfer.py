"""
AcademicTransfer source — scrapes PhD positions from the Netherlands.

Uses AcademicTransfer's sitemap to discover new English-language PhD vacancies,
then scrapes each page for structured details.
"""

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SITEMAP_URL = "https://www.academictransfer.com/sitemap-vacancies.xml"
CRAWL_DELAY = int(__import__("os").environ.get("CRAWL_DELAY", "10"))

HEADERS = {
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
}


def _fetch_sitemap() -> list[str]:
    """Fetch the vacancies sitemap and return all URLs."""
    print("  Fetching sitemap...")
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [loc.text for loc in root.findall(".//s:loc", ns)]
    print(f"    {len(urls)} total vacancy URLs in sitemap")
    return urls


def _filter_phd_urls(urls: list[str]) -> list[str]:
    """Keep only English-language PhD vacancy URLs."""
    phd = [u for u in urls if "/en/jobs/" in u and "phd" in u.lower()]
    print(f"    {len(phd)} are English-language PhD positions (by URL slug)")
    return phd


def _extract_id(url: str) -> str:
    m = re.search(r"/jobs/(\d+)/", url)
    return m.group(1) if m else url


def _scrape_vacancy(url: str) -> dict | None:
    """Scrape one vacancy page. Returns structured dict or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"      ✗ {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)

    employer = ""
    for emp_link in soup.find_all("a", href=re.compile(r"/employer/")):
        text = emp_link.get_text(strip=True)
        if text and len(text) < 100 and "applied sciences" not in text.lower():
            employer = text
            break

    main = soup.find("main") or soup.find("article") or soup
    for junk in main.find_all(["nav", "footer"]):
        junk.decompose()
    for junk in main.find_all(class_=re.compile(r"cookie|consent|banner|Footer", re.I)):
        junk.decompose()
    parts = []
    for el in main.find_all(["p", "li", "h2", "h3", "h4"]):
        t = el.get_text(strip=True)
        if t and len(t) > 5:
            parts.append(t)
    description = "\n".join(parts)

    metadata = {}
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            metadata[dt.get_text(strip=True).lower().rstrip(":")] = dd.get_text(strip=True)

    deadline = metadata.get("deadline", metadata.get("closing date", ""))
    for span in soup.find_all("span"):
        t = span.get_text(strip=True)
        if t.startswith("Deadline"):
            deadline = t.replace("Deadline", "").strip()
            break

    days_remaining = ""
    for div in soup.find_all("div", class_="whitespace-nowrap"):
        t = div.get_text(strip=True)
        if "remaining" in t.lower():
            days_remaining = t
            break

    research_fields = ""
    for p in soup.find_all("p"):
        if p.get_text(strip=True) == "Research fields":
            nxt = p.find_next_sibling()
            if nxt:
                research_fields = nxt.get_text(strip=True)
            else:
                research_fields = p.parent.get_text(strip=True).replace("Research fields", "").strip()
            break

    return {
        "id": _extract_id(url),
        "source": "academictransfer",
        "type": "phd",
        "url": url,
        "title": title,
        "employer": employer,
        "country": "NL",
        "country_name": "Netherlands",
        "department": metadata.get("department", metadata.get("faculty", "")),
        "location": metadata.get("location", metadata.get("city", "")),
        "deadline": deadline,
        "days_remaining": days_remaining,
        "research_fields": research_fields,
        "description": description[:8000],
        "metadata": metadata,
        "scraped_at": datetime.now().isoformat(),
    }


def scrape(seen_ids: set[str], max_new: int = 0) -> list[dict]:
    """
    Scrape new PhD positions from AcademicTransfer.

    Returns list of vacancy dicts with standard schema.
    """
    print("\n── AcademicTransfer ──────────────────────────────────")

    urls = _fetch_sitemap()
    phd_urls = _filter_phd_urls(urls)

    new_urls = [u for u in phd_urls if _extract_id(u) not in seen_ids]
    print(f"    {len(new_urls)} are new (not previously seen)")

    if max_new > 0:
        new_urls = new_urls[:max_new]
        print(f"    Limiting to {max_new} for this run")

    vacancies = []
    for i, url in enumerate(new_urls):
        slug = url.rstrip("/").split("/")[-1][:60]
        print(f"    [{i+1}/{len(new_urls)}] {slug}...")
        vacancy = _scrape_vacancy(url)
        if vacancy is None:
            continue

        vacancies.append(vacancy)
        seen_ids.add(vacancy["id"])

        if i < len(new_urls) - 1:
            time.sleep(CRAWL_DELAY)

    print(f"  ✓ {len(vacancies)} new vacancies from AcademicTransfer")
    return vacancies
