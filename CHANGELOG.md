# Changelog

## 2026-08-21

- Added an expired-opportunity archive. Vacancies past their deadline are hidden from the active sections but retained in the data store and shown in a collapsed archive.
- Improved matching for social data science and computational social research, including information spread, bots, inauthentic content, online platforms, quantitative research, and time-series analysis.
- Fixed section classification so the site uses the Groq score when available and the keyword score when Groq is unavailable.
- Rebuilt and deployed the static report through the existing GitHub Actions and Hostinger workflow.

## Earlier

- Added AcademicTransfer scraping, weighted keyword scoring, optional Groq scoring, dismissal controls, and static HTML generation.
- Added automated deployment of the generated `docs/` site to Hostinger via FTPS.
