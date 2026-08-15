"""
Your research profile — edit these lists to tune what the scraper catches.

The system is intentionally biased toward FALSE POSITIVES.
It's better to show you something irrelevant than to miss a good opportunity.
You review the results and dismiss what doesn't fit.
"""

# ── Keywords ────────────────────────────────────────────────────────────────
# A vacancy matching ANY of these scores points. More matches = higher score.
# Organised into families so you can tune them independently.

JOURNALISM_MEDIA = [
    "journalism", "data journalism", "computational journalism",
    "digital journalism", "investigative journalism",
    "news media", "newsroom", "media studies", "media production",
    "documentary", "fact-checking", "fact checking", "factcheck",
    "OSINT", "open source intelligence",
    "political communication", "media communication",
    "public interest media", "the press", "broadcasting",
    "journalistic", "journalism studies",
]

TECH_DATA = [
    "data science", "computational social science", "computational methods",
    "artificial intelligence", "machine learning", "deep learning",
    "NLP", "natural language processing", "large language models", "LLM",
    "computer vision", "multimodal",
    "social media analysis", "web scraping", "data scraping",
    "information retrieval", "network analysis", "data mining",
    "data visualization", "data visualisation", "visual analytics",
    "human-computer interaction", "HCI",
    "digital methods", "digital humanities",
    "text mining", "text analysis", "sentiment analysis",
    "topic modeling", "topic modelling",
]

JOURNALISM_ADJACENT = [
    "misinformation", "disinformation", "fake news",
    "algorithmic accountability", "platform governance",
    "digital democracy", "online political communication",
    "political polarization", "political polarisation",
    "media literacy", "media effects",
    "online discourse", "digital public sphere", "public sphere",
    "civic technology", "civic tech", "public interest technology",
    "AI and society", "responsible AI", "AI ethics",
    "content moderation", "online harm", "hate speech",
    "propaganda", "information disorder", "information ecosystem",
    "democratic", "democracy", "elections",
    "transparency", "accountability", "governance",
    "freedom of expression", "censorship",
    # AI governance & regulation
    "AI governance", "AI regulation", "AI policy",
    "algorithmic fairness", "algorithmic bias", "algorithmic transparency",
    "algorithmic auditing", "algorithm auditing",
    "automated decision", "automated decision-making",
    "digital rights", "data governance", "data ethics",
    "trustworthy AI", "explainable AI", "explainability",
    "AI Act", "digital regulation", "tech regulation",
    "surveillance", "privacy", "data protection",
    "public value", "public values",
    "digital sovereignty", "digital inclusion",
]

YOUR_SKILLS = [
    # These boost score when present but aren't required
    "Python", "SQL", "GIS", "geospatial",
    "D3", "interactive", "visuali",  # catches visualisation/visualization
    "scraping", "web development",
    # "R" is too short — matches everywhere. Use r"\bR\b" style via REGEX_SKILLS.
]

# Terms that need word-boundary matching (short words that cause false positives)
REGEX_SKILLS = [
    r"\bR\b",          # R language, but not "Research", "Results", etc.
    r"\bNLP\b",
    r"\bHCI\b",
    r"\bGIS\b",
    r"\bLLM\b",
]

# ── Weights ──────────────────────────────────────────────────────────────────
# How many points each keyword family contributes per match.
# Journalism terms are weighted highest because that's the core.
WEIGHTS = {
    "journalism_media": 15,
    "tech_data": 8,
    "journalism_adjacent": 10,
    "your_skills": 3,
}

# Minimum score to appear in the report. Set LOW to bias toward false positives.
MIN_SCORE = 5

# ── Hard exclusions ──────────────────────────────────────────────────────────
# Vacancies matching these are dropped entirely. Be conservative — only add
# terms that are NEVER relevant. When in doubt, leave them out.
EXCLUDE_TERMS = [
    "dentistry", "veterinary", "veterinaire", "dental", "orthodontic",
    "cardiovascular", "cardiology", "cardiac",
    "oncology", "tumor", "tumour",
    "molecular biology", "biochemistry", "organic chemistry",
    "structural engineering", "civil engineering", "mechanical engineering",
    "astrophysics", "quantum physics", "particle physics", "quantum mechanics",
    "plant science", "botany", "marine biology", "ecology",
    "pharmacology", "pharmaceutical", "clinical trial",
    "pathology", "radiology", "surgery", "surgical",
    "genomics", "proteomics", "metabolomics",
    "livestock", "poultry", "crop", "soil science",
    "semiconductor", "photonics", "optics",
    "fluid mechanics", "thermodynamics", "aerodynamics",
]

# ── Your profile (used in report, not in scoring) ───────────────────────────
PROFILE_SUMMARY = (
    "MSc Computational & Data Journalism · Python/R/SQL · "
    "data visualisation · web scraping · GIS · NLP · "
    "interactive storytelling · public-interest technology"
)
