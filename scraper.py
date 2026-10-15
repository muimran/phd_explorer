"""
PhD Explorer — multi-source PhD vacancy radar.

Scrapes PhD positions from multiple sources, scores them with keywords
and LLM, and generates a static HTML site with filtering.

Usage:
    python scraper.py                  # scrape + score + generate site
    python scraper.py --scrape-only    # just scrape, skip scoring
    python scraper.py --max 10         # limit to 10 new vacancies (for testing)
    python scraper.py --rebuild        # rebuild site from existing data
"""

import argparse
import json
import re
from datetime import datetime, date
from pathlib import Path

from profile import (
    JOURNALISM_MEDIA, TECH_DATA, JOURNALISM_ADJACENT, YOUR_SKILLS,
    REGEX_SKILLS, WEIGHTS, MIN_SCORE, EXCLUDE_TERMS, PROFILE_SUMMARY,
)
from sources import academictransfer, euraxess

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
VACANCIES_FILE = DATA_DIR / "vacancies.jsonl"
SEEN_FILE = DATA_DIR / "seen_ids.json"
DISMISSED_FILE = DATA_DIR / "dismissed.json"
SITE_DIR = Path(__file__).parent / "docs"

# All enabled source modules
SOURCES = [academictransfer, euraxess]


# ── Seen IDs ────────────────────────────────────────────────────────────────

def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(ids: set[str]):
    SEEN_FILE.write_text(json.dumps(sorted(ids)))


# ── Scoring ──────────────────────────────────────────────────────────────────

def is_postdoc(vacancy: dict) -> bool:
    """Detect postdoc positions — we're looking for PhD only."""
    title = vacancy["title"].lower()
    return any(t in title for t in ["postdoc", "post-doc", "postdoctoral", "post doc"])


def _normalize_employer(emp: str) -> str:
    """Normalize employer name for dedup comparison."""
    emp = emp.lower().strip()
    emp = emp.replace(" via academictransfer", "").replace("academictransfer", "")
    emp = emp.strip().rstrip(",").strip()
    return emp


def deduplicate(vacancies: list[dict]) -> list[dict]:
    """Remove duplicate vacancies: same ID, or same title+employer across/within sources."""
    from difflib import SequenceMatcher

    # Pass 1: deduplicate by ID (keep latest scraped)
    by_id = {}
    for v in vacancies:
        vid = v["id"]
        if vid not in by_id or v.get("scraped_at", "") > by_id[vid].get("scraped_at", ""):
            by_id[vid] = v
    unique = list(by_id.values())

    # Pass 2: deduplicate by title+employer similarity (keep latest)
    unique.sort(key=lambda v: v.get("scraped_at", ""), reverse=True)
    kept = []
    for v in unique:
        title = v["title"].lower().strip()
        emp = _normalize_employer(v.get("employer", ""))
        is_dupe = False
        for k in kept:
            kt = k["title"].lower().strip()
            ke = _normalize_employer(k.get("employer", ""))
            title_sim = SequenceMatcher(None, title, kt).ratio()
            if title_sim > 0.95:
                is_dupe = True
                break
            if title_sim > 0.90:
                if not emp or not ke or SequenceMatcher(None, emp, ke).ratio() > 0.5:
                    is_dupe = True
                    break
        if not is_dupe:
            kept.append(v)

    removed = len(vacancies) - len(kept)
    if removed:
        print(f"  Dedup: removed {removed} duplicates ({len(vacancies)} → {len(kept)})")
    return kept


def hard_exclude(vacancy: dict) -> bool:
    """Exclude clearly unrelated fields unless there is a core social/media signal."""
    text = (vacancy["title"] + " " + vacancy["description"]).lower()
    has_exclusion = any(term.lower() in text for term in EXCLUDE_TERMS)
    if not has_exclusion:
        return False

    core_terms = JOURNALISM_MEDIA + [
        "computational social science", "algorithmic accountability",
        "platform governance", "misinformation", "disinformation",
        "online political communication", "digital democracy",
        "media effects", "information disorder", "public sphere",
    ]
    return not any(term.lower() in text for term in core_terms)


def score_vacancy(vacancy: dict) -> dict:
    """Score a vacancy using weighted keyword matching. Returns enriched dict."""
    title_text = vacancy["title"].lower()
    desc_text = vacancy["description"].lower()
    full_text = title_text + " " + desc_text

    TITLE_BONUS = 3

    matches = {"journalism_media": [], "tech_data": [], "journalism_adjacent": [], "your_skills": []}
    term_in_title = set()

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

    full_raw = vacancy["title"] + " " + vacancy["description"]
    for pattern in REGEX_SKILLS:
        if re.search(pattern, full_raw):
            matches["your_skills"].append(pattern)
            if re.search(pattern, vacancy["title"]):
                term_in_title.add(pattern)

    score = 0
    for family_name, matched in matches.items():
        for term in set(matched):
            base = WEIGHTS[family_name]
            if term in term_in_title:
                score += base * TITLE_BONUS
            else:
                score += base
    score = min(score, 100)

    has_journalism = bool(matches["journalism_media"])
    if not has_journalism and score > 0:
        score = score // 2

    fields = vacancy.get("research_fields", "").lower()
    if "engineering" in fields:
        other_fields = fields.replace("engineering", "").strip("; ,")
        if not other_fields:
            has_title_match = bool(term_in_title - {p for p in REGEX_SKILLS})
            if not has_title_match:
                score = 0

    if score >= 50:
        rec = "APPLY"
    elif score >= MIN_SCORE:
        rec = "INVESTIGATE"
    else:
        rec = "IGNORE"

    all_matched = []
    for family_name, matched in matches.items():
        for term in matched:
            clean = re.sub(r'\\b', '', term) if term.startswith(r'\b') else term
            all_matched.append(clean)

    return {
        **vacancy,
        "score": score,
        "recommendation": rec,
        "matched_terms": sorted(set(all_matched)),
        "match_families": {k: sorted(set(v)) for k, v in matches.items() if v},
    }


# ── HTML report ──────────────────────────────────────────────────────────────

def load_dismissed() -> set[str]:
    if DISMISSED_FILE.exists():
        return set(json.loads(DISMISSED_FILE.read_text()))
    return set()


def deadline_date(value: str) -> date | None:
    """Parse various deadline formats into a date."""
    if not value:
        return None
    normalized = value.replace("'", "'").replace("’", "'").strip()
    for fmt in ("%d %b '%y", "%d %B '%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    return None


# Source display names and colours
SOURCE_META = {
    "academictransfer": {"label": "AcademicTransfer", "color": "#2563eb"},
    "euraxess": {"label": "EURAXESS", "color": "#059669"},
}


def generate_site(scored: list[dict]):
    """Generate a static HTML site in /docs."""
    SITE_DIR.mkdir(exist_ok=True)

    now = datetime.now()
    updated_str = now.strftime("%d %B %Y, %H:%M")
    dismissed = load_dismissed()

    scored = [s for s in scored if s["id"] not in dismissed]
    active = []
    expired = []
    for vacancy in scored:
        deadline = deadline_date(vacancy.get("deadline", ""))
        if deadline and deadline < date.today():
            expired.append(vacancy)
        else:
            active.append(vacancy)
    scored = active
    expired = sorted(expired, key=lambda x: deadline_date(x.get("deadline", "")) or date.min, reverse=True)

    def primary_score(v):
        groq_score = v.get("groq_score")
        return groq_score if groq_score is not None and groq_score >= 0 else v["score"]

    scored = sorted(scored, key=lambda x: (primary_score(x), x["score"]), reverse=True)

    strong_list = [s for s in scored if primary_score(s) >= 60]
    investigate_list = [s for s in scored if 30 <= primary_score(s) < 60]
    weak_list = [s for s in scored if 0 < primary_score(s) < 30 and s["recommendation"] != "IGNORE"]
    zero_list = sorted(
        [s for s in scored if primary_score(s) == 0],
        key=lambda x: x.get("scraped_at", ""),
        reverse=True,
    )

    # Collect unique sources and countries for filter buttons
    all_items = scored + expired
    sources_used = sorted({v.get("source", "academictransfer") for v in all_items})
    countries_used = sorted({v.get("country", "") for v in all_items if v.get("country")})

    def vacancy_card(v, show_added=False):
        kw_score = v["score"]
        groq_score = v.get("groq_score")
        groq_reason = v.get("groq_reason", "")
        clean_terms = [re.sub(r'\\b', '', t) if t.startswith(r'\b') else t for t in v.get("matched_terms", [])[:8]]
        terms = ", ".join(clean_terms)
        families = v.get("match_families", {})
        scraped_at = v.get("scraped_at", "")
        source = v.get("source", "academictransfer")
        country = v.get("country", "")
        country_name = v.get("country_name", country)
        source_info = SOURCE_META.get(source, {"label": source, "color": "#737373"})

        try:
            added_label = datetime.fromisoformat(scraped_at).strftime("%d %b %Y")
        except (TypeError, ValueError):
            added_label = "Unknown"

        fam_parts = []
        if families.get("journalism_media"):
            fam_parts.append("Journalism")
        if families.get("tech_data"):
            fam_parts.append("Tech & Data")
        if families.get("journalism_adjacent"):
            fam_parts.append("Adjacent")
        fam_str = " · ".join(fam_parts)

        deadline_html = ""
        if v.get("deadline"):
            deadline_html = f'<div class="countdown" data-deadline="{v["deadline"]}" title="{v["deadline"]}"></div>'

        employer_html = ""
        if v.get("employer"):
            employer_html = f'{v["employer"]}'

        if groq_score is not None and groq_score >= 0:
            score_html = f'<div class="score" title="{groq_reason}">{groq_score}</div>'
            score_html += f'<div class="kw-score" title="Keyword score">kw {kw_score}</div>'
        else:
            score_html = f'<div class="score">{kw_score}</div>'
            score_html += f'<div class="kw-score">kw</div>'

        meta_text = employer_html
        if show_added:
            meta_text += f" · Added {added_label}"

        # Source and country badges
        badge_html = f'<span class="source-badge" style="border-color:{source_info["color"]};color:{source_info["color"]}">{source_info["label"]}</span>'
        if country:
            badge_html += f' <span class="country-badge">{country}</span>'

        return f"""
        <div class="card" data-id="{v['id']}" data-source="{source}" data-country="{country}">
            <div class="card-top">
                <div class="score-col">
                    {score_html}
                </div>
                <div class="card-body">
                    <div class="badges">{badge_html}</div>
                    <h3><a href="{v['url']}" target="_blank" rel="noopener">{v['title']}</a></h3>
                    <div class="meta">{meta_text}</div>
                    <div class="tags">{fam_str}</div>
                    <div class="terms">{terms}</div>
                </div>
                <div class="card-right">
                    {deadline_html}
                    <button class="dismiss-btn" onclick="dismiss('{v['id']}')" title="Not relevant">✕</button>
                </div>
            </div>
        </div>"""

    cards_html = ""
    if strong_list:
        cards_html += f'<div class="section-header"><h2>Strong matches</h2><span class="section-count">{len(strong_list)}</span><button class="collapse-btn" aria-label="Collapse section">&#x25B2;</button></div>\n'
        for v in strong_list:
            cards_html += vacancy_card(v)
    if investigate_list:
        cards_html += f'<div class="section-header"><h2>Worth investigating</h2><span class="section-count">{len(investigate_list)}</span><button class="collapse-btn" aria-label="Collapse section">&#x25B2;</button></div>\n'
        for v in investigate_list:
            cards_html += vacancy_card(v)
    if weak_list:
        cards_html += f'<div class="section-header"><h2>Weak matches</h2><span class="section-count">{len(weak_list)}</span><button class="collapse-btn" aria-label="Collapse section">&#x25B2;</button></div>\n'
        for v in weak_list:
            cards_html += vacancy_card(v)
    if zero_list:
        zero_cards = "".join(vacancy_card(v, show_added=True) for v in zero_list)
        cards_html += f"""
        <details class="archive zero-archive">
            <summary>Zero-score matches <span class="section-count">{len(zero_list)}</span></summary>
            <p class="archive-note">These low-confidence matches are kept for review and sorted by the date they were added.</p>
            {zero_cards}
        </details>"""

    if not strong_list and not investigate_list and not weak_list and not zero_list:
        cards_html = '<div class="empty">No matching vacancies right now. Check back soon.</div>'

    archive_html = ""
    if expired:
        archive_cards = "".join(vacancy_card(v) for v in expired)
        archive_html = f"""
        <details class="archive">
            <summary>Expired opportunities <span class="section-count">{len(expired)}</span></summary>
            <p class="archive-note">These records are kept for reference but are no longer in the active opportunity list.</p>
            {archive_cards}
        </details>"""

    # Build filter buttons
    source_buttons = ""
    for src in sources_used:
        info = SOURCE_META.get(src, {"label": src, "color": "#737373"})
        source_buttons += f'<button class="filter-btn active" data-filter="source" data-value="{src}" style="--btn-color:{info["color"]}">{info["label"]}</button>\n'

    country_buttons = ""
    for cc in countries_used:
        country_buttons += f'<button class="filter-btn active" data-filter="country" data-value="{cc}">{cc}</button>\n'

    dismissed_json = json.dumps(sorted(dismissed))

    # Source list for footer
    source_labels = [SOURCE_META.get(s, {"label": s})["label"] for s in sources_used]
    source_links = {
        "academictransfer": '<a href="https://www.academictransfer.com">AcademicTransfer</a>',
        "euraxess": '<a href="https://euraxess.ec.europa.eu">EURAXESS</a>',
    }
    footer_sources = " · ".join(source_links.get(s, s) for s in sources_used)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PhD Radar</title>
<style>
:root {{
    --bg: #fff;
    --fg: #1a1a1a;
    --border: #e5e5e5;
    --muted: #737373;
    --accent: #7c3aed;
    --radius: 10px;
}}
@media (prefers-color-scheme: dark) {{
    :root {{
        --bg: #111;
        --fg: #e5e5e5;
        --border: #2a2a2a;
        --muted: #888;
        --accent: #a78bfa;
    }}
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--bg); color: var(--fg);
    line-height: 1.5; -webkit-font-smoothing: antialiased;
}}
.container {{ max-width: 680px; margin: 0 auto; padding: 2.5rem 1.5rem 6rem; }}

/* Header */
.header {{ margin-bottom: 2.5rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--border); }}
.header h1 {{ font-size: 1.5rem; font-weight: 600; margin-bottom: 0.25rem; }}
.updated {{ font-size: 0.8rem; color: var(--muted); }}

/* Stats */
.stats {{
    display: flex; gap: 2rem; margin-bottom: 1.5rem;
    padding-bottom: 1.5rem; border-bottom: 1px solid var(--border);
}}
.stat {{ }}
.stat-num {{ font-size: 1.75rem; font-weight: 600; line-height: 1; }}
.stat-num.accent {{ color: var(--accent); }}
.stat-label {{ font-size: 0.75rem; color: var(--muted); margin-top: 0.15rem; }}

/* Filters */
.filters {{
    display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1.5rem;
    padding-bottom: 1.5rem; border-bottom: 1px solid var(--border);
}}
.filter-group {{ display: flex; flex-wrap: wrap; gap: 0.35rem; align-items: center; }}
.filter-group::before {{ font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-right: 0.25rem; }}
.filter-group.source-filters::before {{ content: "Source"; }}
.filter-group.country-filters::before {{ content: "Country"; }}
.filter-sep {{ width: 1px; height: 1.5rem; background: var(--border); margin: 0 0.5rem; }}
.filter-btn {{
    font-size: 0.7rem; font-weight: 500;
    padding: 0.25rem 0.6rem; border-radius: 999px;
    border: 1px solid var(--border); background: var(--bg); color: var(--muted);
    cursor: pointer; transition: all 0.15s;
}}
.filter-btn.active {{
    border-color: var(--btn-color, var(--accent));
    color: var(--btn-color, var(--accent));
    background: color-mix(in srgb, var(--btn-color, var(--accent)) 8%, var(--bg));
}}
.filter-btn:hover {{ opacity: 0.8; }}

/* Sections */
.section-header {{
    display: flex; align-items: center; gap: 0.5rem;
    margin: 2rem 0 0rem; padding: 0.55rem 0;
    position: sticky; top: 0; z-index: 10;
    background: var(--bg); border-bottom: 1px solid var(--border);
}}
.section-header {{ cursor: pointer; }}
.section-header h2 {{ font-size: 0.85rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }}
.section-header.collapsed {{ margin-bottom: 0; }}
.section-count {{
    font-size: 0.7rem; font-weight: 600; color: var(--accent);
    background: none; padding: 0;
}}
.collapse-btn {{
    margin-left: auto; background: none; border: none; cursor: pointer;
    font-size: 0.6rem; color: var(--muted); padding: 0.2rem 0.4rem;
    transition: transform 0.2s ease;
}}
.collapse-btn:hover {{ color: var(--fg); }}
.section-header.collapsed .collapse-btn {{ transform: rotate(180deg); }}
.card.section-collapsed {{ display: none; }}

/* Cards */
.card {{
    border-bottom: 1px solid var(--border);
    padding: 1rem 0;
    transition: opacity 0.3s;
}}
.card:last-child {{ border-bottom: none; }}
.card.dismissed {{ opacity: 0; height: 0; overflow: hidden; padding: 0; border: none; }}
.card.filtered {{ display: none; }}
.card-top {{ display: flex; align-items: flex-start; gap: 1rem; }}
.score-col {{
    display: flex; flex-direction: column; align-items: center;
    gap: 0.2rem; min-width: 36px; padding-top: 0.15rem;
}}
.score {{
    font-size: 0.8rem; font-weight: 600; color: var(--accent);
    text-align: center;
}}
.kw-score {{
    font-size: 0.6rem; color: var(--muted); white-space: nowrap;
    text-align: center;
}}
.card-body {{ flex: 1; min-width: 0; }}
.card h3 {{ font-size: 0.95rem; font-weight: 500; line-height: 1.4; margin-bottom: 0.2rem; }}
.card a {{ color: var(--fg); text-decoration: none; }}
.card a:hover {{ color: var(--accent); }}
.badges {{ display: flex; gap: 0.35rem; margin-bottom: 0.3rem; flex-wrap: wrap; }}
.source-badge {{
    font-size: 0.6rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em;
    padding: 0.1rem 0.4rem; border-radius: 3px;
    border: 1px solid; background: transparent;
}}
.country-badge {{
    font-size: 0.6rem; font-weight: 600;
    padding: 0.1rem 0.4rem; border-radius: 3px;
    background: var(--border); color: var(--fg);
}}
.meta {{ font-size: 0.8rem; color: var(--muted); margin-bottom: 0.2rem; }}
.tags {{ font-size: 0.75rem; color: var(--accent); margin-bottom: 0.15rem; }}
.terms {{ font-size: 0.75rem; color: var(--muted); opacity: 0.6; }}
.card-right {{
    display: flex; flex-direction: column; align-items: flex-end;
    gap: 0.5rem; flex-shrink: 0; padding-top: 0.1rem;
}}
.countdown {{
    font-size: 0.75rem; color: var(--muted); white-space: nowrap;
    cursor: default;
}}
.countdown.urgent {{ color: var(--accent); font-weight: 600; }}
.countdown.expired {{ color: var(--border); }}
.dismiss-btn {{
    background: none; border: none;
    color: var(--border); cursor: pointer;
    padding: 0.3rem; font-size: 1.1rem;
    line-height: 1; transition: color 0.15s;
}}
.dismiss-btn:hover {{ color: var(--fg); }}

/* Empty */
.empty {{ text-align: center; padding: 3rem 1rem; color: var(--muted); }}
.archive {{ margin-top: 2rem; border-top: 1px solid var(--border); }}
.archive summary {{ cursor: pointer; list-style: none; padding: 1.25rem 0; font-size: 0.95rem; font-weight: 500; }}
.archive summary::-webkit-details-marker {{ display: none; }}
.archive summary::before {{ content: '>'; display: inline-block; margin-right: 0.5rem; color: var(--muted); }}
.archive[open] summary::before {{ transform: rotate(90deg); }}
.archive-note {{ margin: -0.4rem 0 1rem; color: var(--muted); font-size: 0.8rem; }}


/* Sync bar */
#sync-bar {{
    display: none; position: fixed; bottom: 0; left: 0; right: 0;
    background: var(--bg); border-top: 1px solid var(--border);
    padding: 0.75rem 1.5rem; z-index: 100;
}}
#sync-bar .inner {{
    max-width: 680px; margin: 0 auto;
    display: flex; align-items: center; justify-content: space-between;
    font-size: 0.8rem; color: var(--muted);
}}
.sync-actions {{ display: flex; gap: 0.5rem; }}
.sync-btn {{
    background: var(--accent); color: #fff; border: none;
    border-radius: 6px; padding: 0.4rem 1rem;
    cursor: pointer; font-size: 0.8rem; font-weight: 500;
}}
.sync-btn:hover {{ opacity: 0.85; }}
.sync-btn.secondary {{
    background: transparent; color: var(--muted); border: 1px solid var(--border);
}}
.count {{ font-weight: 600; color: var(--fg); }}

/* Footer */
.footer {{
    margin-top: 2.5rem; padding-top: 1.5rem;
    border-top: 1px solid var(--border);
    font-size: 0.75rem; color: var(--muted);
}}
.footer a {{ color: var(--muted); text-decoration: none; }}
.footer a:hover {{ color: var(--accent); }}

@media (max-width: 480px) {{
    .container {{ padding: 1.5rem 1rem 6rem; }}
    .stats {{ gap: 1.5rem; }}
    .stat-num {{ font-size: 1.4rem; }}
}}
</style>
</head>
<body>
<div class="container">

<div class="header">
    <h1>PhD Radar</h1>
    <div class="updated">Last scraped {updated_str}</div>
</div>

<div class="stats">
    <div class="stat">
        <div class="stat-num">{len(scored)}</div>
        <div class="stat-label">Total</div>
    </div>
    <div class="stat">
        <div class="stat-num accent">{len(strong_list)}</div>
        <div class="stat-label">Strong</div>
    </div>
    <div class="stat">
        <div class="stat-num">{len(investigate_list)}</div>
        <div class="stat-label">Investigate</div>
    </div>
    <div class="stat">
        <div class="stat-num">{len(zero_list)}</div>
        <div class="stat-label">Zero score</div>
    </div>
</div>

<div class="filters">
    <div class="filter-group source-filters">
        {source_buttons}
    </div>
    <div class="filter-sep"></div>
    <div class="filter-group country-filters">
        {country_buttons}
    </div>
</div>

{cards_html}
{archive_html}

<div id="sync-bar">
    <div class="inner">
        <span><span class="count" id="dismiss-count">0</span> dismissed</span>
        <div class="sync-actions">
            <button class="sync-btn secondary" onclick="undoAll()">Undo</button>
            <button class="sync-btn" onclick="syncDismissals()">Save</button>
        </div>
    </div>
</div>

<div class="footer">
    Sources: {footer_sources} · Updates every 2 days · AI scored by Llama 3.3
</div>

</div>

<script>
// ── Filters ──
document.querySelectorAll('.filter-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
        btn.classList.toggle('active');
        applyFilters();
    }});
}});

function applyFilters() {{
    const activeSources = new Set();
    const activeCountries = new Set();
    document.querySelectorAll('.filter-btn[data-filter="source"].active').forEach(b => activeSources.add(b.dataset.value));
    document.querySelectorAll('.filter-btn[data-filter="country"].active').forEach(b => activeCountries.add(b.dataset.value));

    document.querySelectorAll('.card').forEach(card => {{
        const src = card.dataset.source || 'academictransfer';
        const country = card.dataset.country || '';
        const sourceOk = activeSources.size === 0 || activeSources.has(src);
        const countryOk = activeCountries.size === 0 || activeCountries.has(country);
        card.classList.toggle('filtered', !(sourceOk && countryOk));
    }});

    // Update section counts
    document.querySelectorAll('.section-header').forEach(header => {{
        let count = 0;
        let el = header.nextElementSibling;
        while (el && !el.classList.contains('section-header') && !el.classList.contains('archive')) {{
            if (el.classList.contains('card') && !el.classList.contains('filtered') && !el.classList.contains('dismissed')) count++;
            el = el.nextElementSibling;
        }}
        const badge = header.querySelector('.section-count');
        if (badge) badge.textContent = count;
    }});
    // Update archive/details counts
    document.querySelectorAll('details.archive').forEach(det => {{
        const visible = det.querySelectorAll('.card:not(.filtered):not(.dismissed)').length;
        const badge = det.querySelector('summary .section-count');
        if (badge) badge.textContent = visible;
    }});
}}

// ── Section collapse ──
document.querySelectorAll('.section-header').forEach(header => {{
    header.addEventListener('click', () => {{
        const collapsed = header.classList.toggle('collapsed');
        let el = header.nextElementSibling;
        while (el && !el.classList.contains('section-header') && !el.classList.contains('archive')) {{
            if (el.classList.contains('card')) el.classList.toggle('section-collapsed', collapsed);
            el = el.nextElementSibling;
        }}
    }});
}});

// ── Deadlines ──
document.querySelectorAll('.countdown').forEach(el => {{
    const raw = el.dataset.deadline;
    if (!raw) return;
    // Handle ISO dates (YYYY-MM-DD) and AT-style ("28 Oct '26")
    let deadline;
    if (/^\\d{{4}}-\\d{{2}}-\\d{{2}}/.test(raw)) {{
        deadline = new Date(raw);
    }} else {{
        const normalized = raw.replace(/['\\u2018\\u2019](\\d{{2}})/, '20$1');
        deadline = new Date(normalized);
    }}
    if (isNaN(deadline)) return;
    const now = new Date();
    now.setHours(0,0,0,0);
    deadline.setHours(0,0,0,0);
    const diff = Math.ceil((deadline - now) / (1000 * 60 * 60 * 24));
    if (diff < 0) {{
        const card = el.closest('.card');
        if (card && !card.closest('.archive')) card.classList.add('dismissed');
        el.textContent = 'Expired';
        el.classList.add('expired');
        return;
    }} else if (diff === 0) {{
        el.textContent = 'Today';
        el.classList.add('urgent');
    }} else if (diff === 1) {{
        el.textContent = '1 day';
        el.classList.add('urgent');
    }} else {{
        el.textContent = diff + ' days';
        if (diff <= 7) el.classList.add('urgent');
    }}
}});

// ── Dismissals ──
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
    """Scrape all new PhD vacancies from all sources."""
    DATA_DIR.mkdir(exist_ok=True)

    seen = load_seen()
    all_vacancies = []

    for source_module in SOURCES:
        try:
            vacancies = source_module.scrape(seen, max_new=max_new)
        except Exception as e:
            print(f"  ✗ Source {source_module.__name__} failed: {e}")
            continue

        # Score each vacancy
        for v in vacancies:
            if is_postdoc(v):
                slug = v["title"][:60]
                print(f"    → excluded (postdoc): {slug}")
                continue
            if hard_exclude(v):
                slug = v["title"][:60]
                print(f"    → excluded (hard filter): {slug}")
                continue

            scored = score_vacancy(v)
            all_vacancies.append(scored)

    # Append to JSONL
    with open(VACANCIES_FILE, "a") as f:
        for v in all_vacancies:
            f.write(json.dumps(v) + "\n")

    save_seen(seen)

    relevant = [v for v in all_vacancies if v["score"] >= MIN_SCORE]
    print(f"\n✓ Scraped {len(all_vacancies)} new vacancies → {len(relevant)} relevant (score ≥ {MIN_SCORE})")
    return relevant


def main():
    parser = argparse.ArgumentParser(description="PhD Explorer — multi-source PhD vacancy radar")
    parser.add_argument("--scrape-only", action="store_true", help="Scrape without generating site")
    parser.add_argument("--max", type=int, default=0, help="Max new vacancies to scrape (0 = all)")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild site from existing data")
    args = parser.parse_args()

    if args.rebuild:
        if not VACANCIES_FILE.exists():
            print("No vacancies file found.")
            return
        all_v = [json.loads(line) for line in VACANCIES_FILE.read_text().strip().split("\n") if line.strip()]
        # Backfill source/country for legacy vacancies
        for v in all_v:
            if "source" not in v:
                v["source"] = "academictransfer"
            if "country" not in v:
                v["country"] = "NL"
                v["country_name"] = "Netherlands"
        all_v = deduplicate(all_v)
        relevant = [
            v for v in all_v
            if not is_postdoc(v) and (v.get("score", 0) >= MIN_SCORE or v.get("groq_score") == 0)
        ]
        generate_site(relevant)
        return

    relevant = scrape_all(max_new=args.max)

    if not args.scrape_only:
        if VACANCIES_FILE.exists():
            all_v = [json.loads(line) for line in VACANCIES_FILE.read_text().strip().split("\n") if line.strip()]
            # Backfill source/country for legacy vacancies
            for v in all_v:
                if "source" not in v:
                    v["source"] = "academictransfer"
                if "country" not in v:
                    v["country"] = "NL"
                    v["country_name"] = "Netherlands"
            all_v = deduplicate(all_v)
            relevant = [
                v for v in all_v
                if not is_postdoc(v) and (v.get("score", 0) >= MIN_SCORE or v.get("groq_score") == 0)
            ]
        generate_site(relevant)


if __name__ == "__main__":
    main()
