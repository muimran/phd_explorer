"""
Job source modules for PhD Explorer — data journalism & media roles.

Each source module exposes a `scrape(seen_ids, max_new)` function that returns
a list of job dicts with fields: id, source, url, title, employer, location,
country, country_name, department, description, posted_date, scraped_at.
"""
