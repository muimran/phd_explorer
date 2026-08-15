# PhD Explorer

Automated radar for Dutch PhD vacancies relevant to computational/data journalism.

## How it works

1. **Scrapes** [AcademicTransfer](https://www.academictransfer.com) sitemap for all PhD vacancy URLs
2. **Fetches** each new vacancy page and extracts structured data
3. **Scores** each vacancy using weighted keyword matching across four families:
   - Journalism & media terms (highest weight)
   - Journalism-adjacent research topics (high weight)
   - Technology & data science terms (medium weight)
   - Your specific skills (low weight, boost only)
4. **Generates** a static HTML report at `docs/index.html` (served via GitHub Pages)

Intentionally biased toward **false positives** — better to show something irrelevant than miss a good opportunity.

## Usage

```bash
pip install -r requirements.txt

# Full run: scrape + score + generate site
python scraper.py

# Test with a few vacancies
python scraper.py --max 5

# Just scrape, don't generate site
python scraper.py --scrape-only

# Rebuild site from existing data
python scraper.py --rebuild
```

## Automation

GitHub Actions runs the scraper daily at 07:00 UTC and commits results.
Enable GitHub Pages from the `docs/` folder in your repo settings.

## Customisation

Edit `profile.py` to:
- Add/remove keywords in any family
- Adjust weights
- Change the minimum score threshold
- Add hard exclusion terms

## Architecture

```
AcademicTransfer sitemap
        ↓
  Python (requests + BeautifulSoup)
        ↓
  Weighted keyword scoring
        ↓
  JSONL data store
        ↓
  Static HTML (GitHub Pages)
```

Zero cost. No API keys. No databases. No servers.
