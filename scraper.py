"""
PhD Explorer — scrapes AcademicTransfer for Dutch PhD vacancies.

Usage:
    python scraper.py                  # scrape + score + generate site
    python scraper.py --scrape-only    # just scrape, skip scoring
    python scraper.py --max 10         # limit to 10 new vacancies (for testing)
"""

import argparse
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from profile import (
    JOURNALISM_MEDIA, TECH_DATA, JOURNALISM_ADJACENT, YOUR_SKILLS,
    REGEX_SKILLS, WEIGHTS, MIN_SCORE, EXCLUDE_TERMS, PROFILE_SUMMARY,
)

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
VACANCIES_FILE = DATA_DIR / "vacancies.jsonl"
SEEN_FILE = DATA_DIR / "seen_ids.json"
DISMISSED_FILE = DATA_DIR / "dismissed.json"
SITE_DIR = Path(__file__).parent / "docs"  # GitHub Pages serves from /docs

SITEMAP_URL = "https://www.academictransfer.com/sitemap-vacancies.xml"
CRAWL_DELAY = int(__import__('os').environ.get('CRAWL_DELAY', '10'))  # seconds, per robots.txt

HEADERS = {
    "User-Agent": "Claude-User (PhD opportunity research tool)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
}


# ── Scraping ─────────────────────────────────────────────────────────────────

def fetch_sitemap() -> list[str]:
    """Fetch the vacancies sitemap and return all URLs."""
    print("Fetching sitemap...")
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [loc.text for loc in root.findall(".//s:loc", ns)]
    print(f"  {len(urls)} total vacancy URLs in sitemap")
    return urls


def filter_phd_urls(urls: list[str]) -> list[str]:
    """Keep only English-language PhD vacancy URLs."""
    phd = [u for u in urls if "/en/jobs/" in u and "phd" in u.lower()]
    print(f"  {len(phd)} are English-language PhD positions (by URL slug)")
    return phd


def extract_id(url: str) -> str:
    m = re.search(r"/jobs/(\d+)/", url)
    return m.group(1) if m else url


def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(ids: set[str]):
    SEEN_FILE.write_text(json.dumps(sorted(ids)))


def scrape_vacancy(url: str) -> dict | None:
    """Scrape one vacancy page. Returns structured dict or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"    ✗ {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)

    # Employer — find links to /employer/ pages, skip the nav menu ones
    employer = ""
    for emp_link in soup.find_all("a", href=re.compile(r"/employer/")):
        text = emp_link.get_text(strip=True)
        # Skip generic nav links and very long blurbs
        if text and len(text) < 100 and "applied sciences" not in text.lower():
            employer = text
            break

    # Extract full text from main content area, excluding boilerplate
    main = soup.find("main") or soup.find("article") or soup
    # Remove nav, footer, cookie banners — these contain "Privacy", "Terms", etc.
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

    # Key-value metadata (dl/dt/dd pairs)
    metadata = {}
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            metadata[dt.get_text(strip=True).lower().rstrip(":")] = dd.get_text(strip=True)

    return {
        "id": extract_id(url),
        "url": url,
        "title": title,
        "employer": employer,
        "department": metadata.get("department", metadata.get("faculty", "")),
        "location": metadata.get("location", metadata.get("city", "")),
        "deadline": metadata.get("deadline", metadata.get("closing date", "")),
        "description": description[:8000],
        "metadata": metadata,
        "scraped_at": datetime.now().isoformat(),
    }


# ── Scoring ──────────────────────────────────────────────────────────────────

def hard_exclude(vacancy: dict) -> bool:
    """True if vacancy is obviously irrelevant."""
    text = (vacancy["title"] + " " + vacancy["description"]).lower()
    return any(term in text for term in EXCLUDE_TERMS)


def score_vacancy(vacancy: dict) -> dict:
    """Score a vacancy using weighted keyword matching. Returns enriched dict."""
    text = (vacancy["title"] + " " + vacancy["description"]).lower()

    matches = {"journalism_media": [], "tech_data": [], "journalism_adjacent": [], "your_skills": []}
    families = [
        ("journalism_media", JOURNALISM_MEDIA),
        ("tech_data", TECH_DATA),
        ("journalism_adjacent", JOURNALISM_ADJACENT),
        ("your_skills", YOUR_SKILLS),
    ]

    for family_name, terms in families:
        for term in terms:
            if term.lower() in text:
                matches[family_name].append(term)

    # Regex-based skill matching (for short terms like R, NLP, etc.)
    for pattern in REGEX_SKILLS:
        if re.search(pattern, vacancy["title"] + " " + vacancy["description"]):
            matches["your_skills"].append(pattern)

    # Score: sum of (unique matches per family × weight), capped at 100
    score = 0
    for family_name, matched in matches.items():
        if matched:
            score += len(set(matched)) * WEIGHTS[family_name]
    score = min(score, 100)

    # Recommendation bucket
    if score >= 50:
        rec = "APPLY"
    elif score >= MIN_SCORE:
        rec = "INVESTIGATE"
    else:
        rec = "IGNORE"

    # Flatten matched terms for display
    all_matched = []
    for family_name, matched in matches.items():
        all_matched.extend(matched)

    return {
        **vacancy,
        "score": score,
        "recommendation": rec,
        "matched_terms": sorted(set(all_matched)),
        "match_families": {k: sorted(set(v)) for k, v in matches.items() if v},
    }


# ── HTML report (GitHub Pages) ───────────────────────────────────────────────

def load_dismissed() -> set[str]:
    """Load dismissed vacancy IDs."""
    if DISMISSED_FILE.exists():
        return set(json.loads(DISMISSED_FILE.read_text()))
    return set()


def generate_site(scored: list[dict]):
    """Generate a static HTML site in /docs for GitHub Pages."""
    SITE_DIR.mkdir(exist_ok=True)

    today = date.today().isoformat()
    dismissed = load_dismissed()

    # Filter out dismissed vacancies
    scored = [s for s in scored if s["id"] not in dismissed]
    scored = sorted(scored, key=lambda x: x["score"], reverse=True)

    apply_list = [s for s in scored if s["recommendation"] == "APPLY"]
    investigate_list = [s for s in scored if s["recommendation"] == "INVESTIGATE"]

    def vacancy_card(v):
        score = v["score"]
        terms = ", ".join(v["matched_terms"][:10])
        families = v.get("match_families", {})

        if score >= 50:
            badge_color = "#d32f2f"
            badge_label = "Apply"
        else:
            badge_color = "#f57c00"
            badge_label = "Investigate"

        family_badges = ""
        if families.get("journalism_media"):
            family_badges += '<span class="fam fam-j">journalism</span>'
        if families.get("tech_data"):
            family_badges += '<span class="fam fam-t">tech/data</span>'
        if families.get("journalism_adjacent"):
            family_badges += '<span class="fam fam-a">adjacent</span>'

        deadline_html = ""
        if v.get("deadline"):
            deadline_html = f'<div class="deadline">Deadline: {v["deadline"]}</div>'

        return f"""
        <div class="card" data-id="{v['id']}">
            <div class="card-header">
                <span class="badge" style="background:{badge_color}">{badge_label} · {score}</span>
                {family_badges}
                <button class="dismiss-btn" onclick="dismiss('{v['id']}')" title="Not relevant">✕</button>
            </div>
            <h3><a href="{v['url']}" target="_blank" rel="noopener">{v['title']}</a></h3>
            <div class="employer">{v['employer']}</div>
            {deadline_html}
            <div class="terms">Matched: {terms}</div>
        </div>"""

    cards_html = ""
    if apply_list:
        cards_html += '<h2 id="apply">🔴 Strong matches</h2>\n'
        for v in apply_list:
            cards_html += vacancy_card(v)
    if investigate_list:
        cards_html += '<h2 id="investigate">🟠 Worth investigating</h2>\n'
        for v in investigate_list:
            cards_html += vacancy_card(v)

    # Embed the already-dismissed IDs so JS can merge with localStorage
    dismissed_json = json.dumps(sorted(dismissed))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PhD Radar — {today}</title>
<style>
:root {{
    --bg: #fafafa; --fg: #222; --card-bg: #fff; --card-border: #e0e0e0;
    --link: #1565c0; --muted: #666;
}}
@media (prefers-color-scheme: dark) {{
    :root {{
        --bg: #181a1b; --fg: #d4d4d4; --card-bg: #242628;
        --card-border: #3a3a3a; --link: #64b5f6; --muted: #999;
    }}
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: var(--bg); color: var(--fg); max-width: 800px;
       margin: 0 auto; padding: 1rem; line-height: 1.5; }}
h1 {{ margin-bottom: 0.25rem; }}
.subtitle {{ color: var(--muted); margin-bottom: 1.5rem; }}
h2 {{ margin: 2rem 0 1rem; }}
.card {{ background: var(--card-bg); border: 1px solid var(--card-border);
         border-radius: 8px; padding: 1rem; margin-bottom: 1rem;
         transition: opacity 0.3s, max-height 0.3s; }}
.card.dismissed {{ opacity: 0.3; max-height: 0; overflow: hidden; padding: 0; margin: 0;
                   border: none; }}
.card h3 {{ margin: 0.5rem 0 0.25rem; font-size: 1.1rem; }}
.card a {{ color: var(--link); text-decoration: none; }}
.card a:hover {{ text-decoration: underline; }}
.card-header {{ display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }}
.badge {{ color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; }}
.dismiss-btn {{ margin-left: auto; background: none; border: 1px solid var(--card-border);
                border-radius: 4px; color: var(--muted); cursor: pointer; padding: 2px 8px;
                font-size: 0.8rem; }}
.dismiss-btn:hover {{ background: #d32f2f; color: #fff; border-color: #d32f2f; }}
.fam {{ font-size: 0.7rem; padding: 2px 6px; border-radius: 3px; }}
.fam-j {{ background: #e8f5e9; color: #2e7d32; }}
.fam-t {{ background: #e3f2fd; color: #1565c0; }}
.fam-a {{ background: #fff3e0; color: #e65100; }}
@media (prefers-color-scheme: dark) {{
    .fam-j {{ background: #1b5e20; color: #a5d6a7; }}
    .fam-t {{ background: #0d47a1; color: #90caf9; }}
    .fam-a {{ background: #bf360c; color: #ffcc80; }}
}}
.employer {{ color: var(--muted); }}
.deadline {{ color: var(--muted); font-size: 0.9rem; }}
.terms {{ font-size: 0.85rem; color: var(--muted); margin-top: 0.5rem; }}
.stats {{ background: var(--card-bg); border: 1px solid var(--card-border);
          border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem;
          display: flex; gap: 2rem; flex-wrap: wrap; }}
.stat {{ text-align: center; }}
.stat-num {{ font-size: 1.8rem; font-weight: 700; }}
.stat-label {{ font-size: 0.8rem; color: var(--muted); }}
#sync-bar {{ display: none; position: fixed; bottom: 0; left: 0; right: 0;
             background: var(--card-bg); border-top: 2px solid #d32f2f;
             padding: 0.75rem 1rem; text-align: center; z-index: 100; }}
#sync-bar button {{ background: #d32f2f; color: #fff; border: none; border-radius: 4px;
                    padding: 0.5rem 1rem; cursor: pointer; font-size: 0.9rem; margin: 0 0.5rem; }}
#sync-bar button:hover {{ background: #b71c1c; }}
#sync-bar button.secondary {{ background: var(--card-border); color: var(--fg); }}
#sync-bar button.secondary:hover {{ background: var(--muted); color: #fff; }}
#sync-bar .count {{ font-weight: 600; }}
</style>
</head>
<body>
<h1>PhD Radar</h1>
<p class="subtitle">Last updated: {today} · Profile: {PROFILE_SUMMARY}</p>

<div class="stats">
    <div class="stat"><div class="stat-num">{len(scored)}</div><div class="stat-label">Scanned</div></div>
    <div class="stat"><div class="stat-num" style="color:#d32f2f">{len(apply_list)}</div><div class="stat-label">Strong</div></div>
    <div class="stat"><div class="stat-num" style="color:#f57c00">{len(investigate_list)}</div><div class="stat-label">Investigate</div></div>
</div>

{cards_html}

<div id="sync-bar">
    <span class="count" id="dismiss-count">0</span> dismissed this session ·
    <button onclick="syncDismissals()">Save to GitHub</button>
    <button class="secondary" onclick="undoAll()">Undo all</button>
</div>

<footer style="margin-top:3rem;padding-bottom:4rem;color:var(--muted);font-size:0.8rem;">
Source: <a href="https://www.academictransfer.com" style="color:var(--muted)">AcademicTransfer</a>
· Generated by <a href="https://github.com" style="color:var(--muted)">PhD Explorer</a>
</footer>

<script>
// Already-dismissed IDs (from data/dismissed.json, baked in at build time)
const serverDismissed = new Set({dismissed_json});

// Session dismissals (not yet saved to GitHub)
let sessionDismissed = new Set();

// On load: hide any cards dismissed in localStorage (from a previous unsaved session)
const stored = JSON.parse(localStorage.getItem('phd_dismissed') || '[]');
stored.forEach(id => {{
    const card = document.querySelector(`.card[data-id="${{id}}"]`);
    if (card) {{ card.classList.add('dismissed'); sessionDismissed.add(id); }}
}});
updateBar();

function dismiss(id) {{
    const card = document.querySelector(`.card[data-id="${{id}}"]`);
    if (card) card.classList.add('dismissed');
    sessionDismissed.add(id);
    localStorage.setItem('phd_dismissed', JSON.stringify([...sessionDismissed]));
    updateBar();
}}

function undoAll() {{
    sessionDismissed.forEach(id => {{
        const card = document.querySelector(`.card[data-id="${{id}}"]`);
        if (card) card.classList.remove('dismissed');
    }});
    sessionDismissed.clear();
    localStorage.removeItem('phd_dismissed');
    updateBar();
}}

function updateBar() {{
    const bar = document.getElementById('sync-bar');
    const count = document.getElementById('dismiss-count');
    if (sessionDismissed.size > 0) {{
        bar.style.display = 'block';
        count.textContent = sessionDismissed.size;
    }} else {{
        bar.style.display = 'none';
    }}
}}

async function syncDismissals() {{
    // Merge server + session dismissed IDs
    const all = [...new Set([...serverDismissed, ...sessionDismissed])].sort();
    const content = JSON.stringify(all, null, 2);

    // Try GitHub API if token is configured
    let token = localStorage.getItem('phd_github_token');
    let repo = localStorage.getItem('phd_github_repo');

    if (!token || !repo) {{
        // First time: ask for config
        const setup = prompt(
            'To save dismissals to GitHub, enter: owner/repo,your_personal_access_token\\n' +
            '(The token needs "contents" write permission.\\n' +
            'This is stored in your browser only — never sent anywhere else.)\\n\\n' +
            'Or press Cancel to copy the JSON to clipboard instead.'
        );
        if (!setup) {{
            // Fallback: copy to clipboard
            navigator.clipboard.writeText(content).then(() => {{
                alert('Copied! Paste this into data/dismissed.json in your repo.');
            }});
            return;
        }}
        const parts = setup.split(',');
        if (parts.length < 2) {{ alert('Format: owner/repo,token'); return; }}
        repo = parts[0].trim();
        token = parts.slice(1).join(',').trim();
        localStorage.setItem('phd_github_repo', repo);
        localStorage.setItem('phd_github_token', token);
    }}

    try {{
        // Get current file SHA (needed for updates)
        const getResp = await fetch(
            `https://api.github.com/repos/${{repo}}/contents/data/dismissed.json`,
            {{ headers: {{ 'Authorization': `Bearer ${{token}}` }} }}
        );
        let sha = null;
        if (getResp.ok) {{
            const data = await getResp.json();
            sha = data.sha;
        }}

        // Write file
        const body = {{
            message: `Dismiss ${{sessionDismissed.size}} vacancies`,
            content: btoa(content),
        }};
        if (sha) body.sha = sha;

        const putResp = await fetch(
            `https://api.github.com/repos/${{repo}}/contents/data/dismissed.json`,
            {{
                method: 'PUT',
                headers: {{
                    'Authorization': `Bearer ${{token}}`,
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify(body),
            }}
        );

        if (putResp.ok) {{
            localStorage.removeItem('phd_dismissed');
            sessionDismissed.clear();
            updateBar();
            alert('✓ Saved to GitHub! Dismissed vacancies will be hidden on the next build.');
        }} else {{
            const err = await putResp.json();
            throw new Error(err.message || putResp.statusText);
        }}
    }} catch (e) {{
        // If GitHub fails, fall back to clipboard
        if (confirm(`GitHub save failed: ${{e.message}}\\n\\nCopy JSON to clipboard instead?`)) {{
            navigator.clipboard.writeText(content).then(() => {{
                alert('Copied! Paste into data/dismissed.json in your repo.');
            }});
        }}
        // Clear stored credentials if auth failed
        if (e.message.includes('Bad credentials')) {{
            localStorage.removeItem('phd_github_token');
            localStorage.removeItem('phd_github_repo');
        }}
    }}
}}
</script>
</body>
</html>"""

    (SITE_DIR / "index.html").write_text(html)
    print(f"✓ Site generated at {SITE_DIR / 'index.html'}")


# ── Main ─────────────────────────────────────────────────────────────────────

def scrape_all(max_new: int = 0) -> list[dict]:
    """Scrape all new PhD vacancies from AcademicTransfer."""
    DATA_DIR.mkdir(exist_ok=True)

    urls = fetch_sitemap()
    phd_urls = filter_phd_urls(urls)

    seen = load_seen()
    new_urls = [u for u in phd_urls if extract_id(u) not in seen]
    print(f"  {len(new_urls)} are new (not previously seen)")

    if max_new > 0:
        new_urls = new_urls[:max_new]
        print(f"  Limiting to {max_new} for this run")

    vacancies = []
    for i, url in enumerate(new_urls):
        slug = url.rstrip("/").split("/")[-1][:60]
        print(f"  [{i+1}/{len(new_urls)}] {slug}...")
        vacancy = scrape_vacancy(url)
        if vacancy is None:
            continue

        if hard_exclude(vacancy):
            print(f"    → excluded (hard filter)")
            seen.add(vacancy["id"])
            continue

        scored = score_vacancy(vacancy)
        vacancies.append(scored)
        seen.add(vacancy["id"])

        if i < len(new_urls) - 1:
            time.sleep(CRAWL_DELAY)

    # Append to JSONL
    with open(VACANCIES_FILE, "a") as f:
        for v in vacancies:
            f.write(json.dumps(v) + "\n")

    save_seen(seen)

    # Filter to relevant only
    relevant = [v for v in vacancies if v["score"] >= MIN_SCORE]
    print(f"\n✓ Scraped {len(vacancies)} new vacancies → {len(relevant)} relevant (score ≥ {MIN_SCORE})")
    return relevant


def main():
    parser = argparse.ArgumentParser(description="PhD Explorer — Dutch PhD vacancy radar")
    parser.add_argument("--scrape-only", action="store_true", help="Scrape without generating site")
    parser.add_argument("--max", type=int, default=0, help="Max new vacancies to scrape (0 = all)")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild site from existing data")
    args = parser.parse_args()

    if args.rebuild:
        if not VACANCIES_FILE.exists():
            print("No vacancies file found.")
            return
        all_v = [json.loads(line) for line in VACANCIES_FILE.read_text().strip().split("\n") if line.strip()]
        relevant = [v for v in all_v if v.get("score", 0) >= MIN_SCORE]
        generate_site(relevant)
        return

    relevant = scrape_all(max_new=args.max)

    if not args.scrape_only:
        # Include any previously scored relevant vacancies
        if VACANCIES_FILE.exists():
            all_v = [json.loads(line) for line in VACANCIES_FILE.read_text().strip().split("\n") if line.strip()]
            relevant = [v for v in all_v if v.get("score", 0) >= MIN_SCORE]
        generate_site(relevant)


if __name__ == "__main__":
    main()
