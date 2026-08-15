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
    "User-Agent": "PhD-Explorer/1.0 (academic opportunity research tool)",
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
    title_text = vacancy["title"].lower()
    desc_text = vacancy["description"].lower()
    full_text = title_text + " " + desc_text

    # Title multiplier: a keyword in the title is worth 3× a keyword in the body.
    TITLE_BONUS = 3

    matches = {"journalism_media": [], "tech_data": [], "journalism_adjacent": [], "your_skills": []}
    term_in_title = set()   # track which terms appeared in the title

    families = [
        ("journalism_media", JOURNALISM_MEDIA),
        ("tech_data", TECH_DATA),
        ("journalism_adjacent", JOURNALISM_ADJACENT),
        ("your_skills", YOUR_SKILLS),
    ]

    for family_name, terms in families:
        for term in terms:
            low = term.lower()
            if low in full_text:
                matches[family_name].append(term)
                if low in title_text:
                    term_in_title.add(term)

    # Regex-based skill matching (for short terms like R, NLP, etc.)
    full_raw = vacancy["title"] + " " + vacancy["description"]
    for pattern in REGEX_SKILLS:
        if re.search(pattern, full_raw):
            matches["your_skills"].append(pattern)
            if re.search(pattern, vacancy["title"]):
                term_in_title.add(pattern)

    # Score: each unique match contributes its family weight.
    # Terms found in the title get an extra TITLE_BONUS multiplier.
    score = 0
    for family_name, matched in matches.items():
        for term in set(matched):
            base = WEIGHTS[family_name]
            if term in term_in_title:
                score += base * TITLE_BONUS
            else:
                score += base
    score = min(score, 100)

    # Bonus: journalism terms are what make a vacancy truly relevant.
    # A PhD with lots of tech/adjacent matches but ZERO journalism/media
    # connection is probably not your field — halve its score.
    has_journalism = bool(matches["journalism_media"])
    if not has_journalism and score > 0:
        score = score // 2

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
    """Generate a static HTML site in /docs."""
    SITE_DIR.mkdir(exist_ok=True)

    now = datetime.now()
    today = date.today().isoformat()
    updated_str = now.strftime("%d %B %Y, %H:%M")
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
            badge_class = "badge-strong"
            badge_label = "Strong match"
        else:
            badge_class = "badge-investigate"
            badge_label = "Investigate"

        family_badges = ""
        if families.get("journalism_media"):
            family_badges += '<span class="fam fam-j">Journalism</span>'
        if families.get("tech_data"):
            family_badges += '<span class="fam fam-t">Tech & Data</span>'
        if families.get("journalism_adjacent"):
            family_badges += '<span class="fam fam-a">Adjacent</span>'

        deadline_html = ""
        if v.get("deadline"):
            deadline_html = f'<div class="meta-item"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>{v["deadline"]}</div>'

        employer_html = ""
        if v.get("employer"):
            employer_html = f'<div class="meta-item"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><path d="M9 22V12h6v10"/></svg>{v["employer"]}</div>'

        return f"""
        <div class="card" data-id="{v['id']}">
            <div class="card-top">
                <div class="badges">
                    <span class="badge {badge_class}">{badge_label} · {score}</span>
                    {family_badges}
                </div>
                <button class="dismiss-btn" onclick="dismiss('{v['id']}')" title="Not relevant">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
                </button>
            </div>
            <h3><a href="{v['url']}" target="_blank" rel="noopener">{v['title']}</a></h3>
            <div class="card-meta">
                {employer_html}
                {deadline_html}
            </div>
            <div class="terms">{terms}</div>
        </div>"""

    cards_html = ""
    if apply_list:
        cards_html += f'<div class="section-header"><h2>Strong matches</h2><span class="section-count">{len(apply_list)}</span></div>\n'
        for v in apply_list:
            cards_html += vacancy_card(v)
    if investigate_list:
        cards_html += f'<div class="section-header"><h2>Worth investigating</h2><span class="section-count">{len(investigate_list)}</span></div>\n'
        for v in investigate_list:
            cards_html += vacancy_card(v)

    if not apply_list and not investigate_list:
        cards_html = '<div class="empty">No matching vacancies right now. Check back in 3 days.</div>'

    dismissed_json = json.dumps(sorted(dismissed))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PhD Radar</title>
<style>
:root {{
    --bg: #f8f8fc;
    --fg: #1a1a2e;
    --card-bg: #ffffff;
    --card-border: #e8e8f0;
    --card-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06);
    --card-hover: 0 4px 12px rgba(0,0,0,0.08);
    --accent: #673ab7;
    --accent-light: #ede7f6;
    --accent-text: #4a148c;
    --link: #673ab7;
    --muted: #6b7280;
    --muted-light: #9ca3af;
    --green-bg: #e8f5e9; --green-fg: #2e7d32;
    --blue-bg: #e3f2fd; --blue-fg: #1565c0;
    --orange-bg: #fff3e0; --orange-fg: #e65100;
    --red: #e53935;
    --red-light: #ffebee;
    --amber: #f57c00;
    --amber-light: #fff8e1;
    --radius: 12px;
    --radius-sm: 8px;
}}
@media (prefers-color-scheme: dark) {{
    :root {{
        --bg: #0f0f1a;
        --fg: #e0e0e8;
        --card-bg: #1a1a2e;
        --card-border: #2a2a40;
        --card-shadow: 0 1px 3px rgba(0,0,0,0.3);
        --card-hover: 0 4px 12px rgba(0,0,0,0.4);
        --accent: #b39ddb;
        --accent-light: #1a1030;
        --accent-text: #ce93d8;
        --link: #b39ddb;
        --muted: #9ca3af;
        --muted-light: #6b7280;
        --green-bg: #1b3a1b; --green-fg: #a5d6a7;
        --blue-bg: #0d2847; --blue-fg: #90caf9;
        --orange-bg: #3e1a00; --orange-fg: #ffcc80;
        --red: #ef5350;
        --red-light: #2a1215;
        --amber: #ffb74d;
        --amber-light: #2a2000;
    }}
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
    background: var(--bg); color: var(--fg);
    line-height: 1.6; -webkit-font-smoothing: antialiased;
}}
.container {{ max-width: 720px; margin: 0 auto; padding: 2rem 1.25rem 6rem; }}

/* Header */
.header {{ margin-bottom: 2rem; }}
.header h1 {{
    font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em;
    margin-bottom: 0.25rem;
}}
.header h1 span {{ color: var(--accent); }}
.updated {{
    font-size: 0.85rem; color: var(--muted);
    display: flex; align-items: center; gap: 0.4rem;
}}
.updated svg {{ opacity: 0.5; }}

/* Stats row */
.stats {{
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.75rem;
    margin-bottom: 2rem;
}}
.stat-card {{
    background: var(--card-bg); border: 1px solid var(--card-border);
    border-radius: var(--radius); padding: 1.25rem 1rem;
    text-align: center; box-shadow: var(--card-shadow);
}}
.stat-num {{ font-size: 2rem; font-weight: 700; line-height: 1; margin-bottom: 0.25rem; }}
.stat-num.strong {{ color: var(--accent); }}
.stat-num.investigate {{ color: var(--amber); }}
.stat-label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; font-weight: 500; }}

/* Section headers */
.section-header {{
    display: flex; align-items: center; gap: 0.75rem;
    margin: 2rem 0 1rem; padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--card-border);
}}
.section-header h2 {{ font-size: 1.1rem; font-weight: 600; }}
.section-count {{
    background: var(--accent-light); color: var(--accent-text);
    font-size: 0.75rem; font-weight: 600; padding: 2px 10px;
    border-radius: 100px;
}}

/* Cards */
.card {{
    background: var(--card-bg); border: 1px solid var(--card-border);
    border-radius: var(--radius); padding: 1.25rem;
    margin-bottom: 0.75rem; box-shadow: var(--card-shadow);
    transition: box-shadow 0.2s, opacity 0.3s, transform 0.3s;
}}
.card:hover {{ box-shadow: var(--card-hover); }}
.card.dismissed {{
    opacity: 0; transform: scale(0.95); max-height: 0;
    overflow: hidden; padding: 0; margin: 0; border: none;
}}
.card-top {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.5rem; }}
.badges {{ display: flex; gap: 0.4rem; flex-wrap: wrap; }}
.badge {{
    font-size: 0.7rem; font-weight: 600; padding: 3px 10px;
    border-radius: 100px; text-transform: uppercase; letter-spacing: 0.03em;
}}
.badge-strong {{ background: var(--accent-light); color: var(--accent-text); }}
.badge-investigate {{ background: var(--amber-light); color: var(--amber); }}
.card h3 {{ font-size: 1rem; font-weight: 600; line-height: 1.4; margin-bottom: 0.5rem; }}
.card a {{ color: var(--link); text-decoration: none; }}
.card a:hover {{ text-decoration: underline; }}
.card-meta {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.5rem; }}
.meta-item {{
    display: flex; align-items: center; gap: 0.3rem;
    font-size: 0.8rem; color: var(--muted);
}}
.terms {{
    font-size: 0.78rem; color: var(--muted-light);
    padding-top: 0.5rem; border-top: 1px solid var(--card-border);
}}
.dismiss-btn {{
    background: none; border: none; color: var(--muted-light);
    cursor: pointer; padding: 4px; border-radius: 6px;
    transition: all 0.15s; display: flex; align-items: center;
}}
.dismiss-btn:hover {{ background: var(--red-light); color: var(--red); }}

/* Family badges */
.fam {{
    font-size: 0.65rem; font-weight: 500; padding: 2px 8px;
    border-radius: 100px; text-transform: uppercase; letter-spacing: 0.03em;
}}
.fam-j {{ background: var(--green-bg); color: var(--green-fg); }}
.fam-t {{ background: var(--blue-bg); color: var(--blue-fg); }}
.fam-a {{ background: var(--orange-bg); color: var(--orange-fg); }}

/* Empty state */
.empty {{
    text-align: center; padding: 3rem 1rem; color: var(--muted);
    background: var(--card-bg); border-radius: var(--radius);
    border: 1px dashed var(--card-border);
}}

/* Sync bar */
#sync-bar {{
    display: none; position: fixed; bottom: 0; left: 0; right: 0;
    background: var(--card-bg); border-top: 1px solid var(--card-border);
    box-shadow: 0 -4px 12px rgba(0,0,0,0.08);
    padding: 0.75rem 1rem; text-align: center; z-index: 100;
}}
#sync-bar .inner {{
    max-width: 720px; margin: 0 auto;
    display: flex; align-items: center; justify-content: center; gap: 0.75rem;
    font-size: 0.85rem; color: var(--muted);
}}
.sync-btn {{
    background: var(--accent); color: #fff; border: none;
    border-radius: var(--radius-sm); padding: 0.5rem 1.25rem;
    cursor: pointer; font-size: 0.85rem; font-weight: 500;
    transition: opacity 0.15s;
}}
.sync-btn:hover {{ opacity: 0.85; }}
.sync-btn.secondary {{
    background: transparent; color: var(--muted); border: 1px solid var(--card-border);
}}
.sync-btn.secondary:hover {{ background: var(--card-border); }}
.count {{ font-weight: 600; color: var(--fg); }}

/* Footer */
.footer {{
    margin-top: 3rem; padding-top: 1.5rem;
    border-top: 1px solid var(--card-border);
    font-size: 0.78rem; color: var(--muted-light); text-align: center;
}}
.footer a {{ color: var(--muted-light); text-decoration: none; }}
.footer a:hover {{ color: var(--accent); }}

@media (max-width: 480px) {{
    .stats {{ grid-template-columns: repeat(3, 1fr); gap: 0.5rem; }}
    .stat-card {{ padding: 0.75rem 0.5rem; }}
    .stat-num {{ font-size: 1.5rem; }}
    .container {{ padding: 1.25rem 1rem 6rem; }}
}}
</style>
</head>
<body>
<div class="container">

<div class="header">
    <h1>PhD <span>Radar</span></h1>
    <div class="updated">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
        Last updated: {updated_str}
    </div>
</div>

<div class="stats">
    <div class="stat-card">
        <div class="stat-num">{len(scored)}</div>
        <div class="stat-label">Total</div>
    </div>
    <div class="stat-card">
        <div class="stat-num strong">{len(apply_list)}</div>
        <div class="stat-label">Strong</div>
    </div>
    <div class="stat-card">
        <div class="stat-num investigate">{len(investigate_list)}</div>
        <div class="stat-label">Investigate</div>
    </div>
</div>

{cards_html}

<div id="sync-bar">
    <div class="inner">
        <span><span class="count" id="dismiss-count">0</span> dismissed</span>
        <button class="sync-btn" onclick="syncDismissals()">Save to GitHub</button>
        <button class="sync-btn secondary" onclick="undoAll()">Undo</button>
    </div>
</div>

<div class="footer">
    Source: <a href="https://www.academictransfer.com">AcademicTransfer</a> · Updates every 3 days
</div>

</div>

<script>
const serverDismissed = new Set({dismissed_json});
let sessionDismissed = new Set();

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
    const all = [...new Set([...serverDismissed, ...sessionDismissed])].sort();
    const content = JSON.stringify(all, null, 2);
    let token = localStorage.getItem('phd_github_token');
    let repo = localStorage.getItem('phd_github_repo');

    if (!token || !repo) {{
        const setup = prompt(
            'To save dismissals, enter: owner/repo,your_personal_access_token\\n' +
            '(Token needs "contents" write permission. Stored in your browser only.)\\n\\n' +
            'Or press Cancel to copy JSON to clipboard.'
        );
        if (!setup) {{
            navigator.clipboard.writeText(content).then(() => alert('Copied! Paste into data/dismissed.json.'));
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
        const getResp = await fetch(
            `https://api.github.com/repos/${{repo}}/contents/data/dismissed.json`,
            {{ headers: {{ 'Authorization': `Bearer ${{token}}` }} }}
        );
        let sha = null;
        if (getResp.ok) {{ sha = (await getResp.json()).sha; }}

        const body = {{ message: `Dismiss ${{sessionDismissed.size}} vacancies`, content: btoa(content) }};
        if (sha) body.sha = sha;

        const putResp = await fetch(
            `https://api.github.com/repos/${{repo}}/contents/data/dismissed.json`,
            {{ method: 'PUT', headers: {{ 'Authorization': `Bearer ${{token}}`, 'Content-Type': 'application/json' }}, body: JSON.stringify(body) }}
        );

        if (putResp.ok) {{
            localStorage.removeItem('phd_dismissed');
            sessionDismissed.clear();
            updateBar();
            alert('Saved! Dismissed vacancies will be hidden on the next build.');
        }} else {{
            throw new Error((await putResp.json()).message || putResp.statusText);
        }}
    }} catch (e) {{
        if (confirm(`Save failed: ${{e.message}}\\n\\nCopy JSON to clipboard instead?`)) {{
            navigator.clipboard.writeText(content).then(() => alert('Copied!'));
        }}
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
