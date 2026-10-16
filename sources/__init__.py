"""
Source modules for PhD Explorer.

Each source module exposes a `scrape(seen_ids, max_new)` function that returns
a list of vacancy dicts with the standard schema below.
"""

# ISO country codes for display and filtering
COUNTRY_CODES = {
    "afghanistan": "AF", "albania": "AL", "argentina": "AR", "australia": "AU",
    "austria": "AT", "bangladesh": "BD", "belgium": "BE", "brazil": "BR",
    "bulgaria": "BG", "canada": "CA", "china": "CN", "croatia": "HR",
    "cyprus": "CY", "czech republic": "CZ", "czechia": "CZ",
    "denmark": "DK", "egypt": "EG", "estonia": "EE", "finland": "FI",
    "france": "FR", "germany": "DE", "greece": "GR", "hong kong": "HK",
    "hungary": "HU", "iceland": "IS", "india": "IN", "indonesia": "ID",
    "ireland": "IE", "israel": "IL", "italy": "IT", "japan": "JP",
    "kazakhstan": "KZ", "korea": "KR", "latvia": "LV", "lithuania": "LT",
    "luxembourg": "LU", "malaysia": "MY", "malta": "MT", "mexico": "MX", "moldova": "MD",
    "morocco": "MA", "netherlands": "NL", "new zealand": "NZ", "nigeria": "NG",
    "norway": "NO", "pakistan": "PK", "poland": "PL", "portugal": "PT",
    "qatar": "QA", "romania": "RO", "saudi arabia": "SA", "serbia": "RS",
    "singapore": "SG", "slovakia": "SK", "slovenia": "SI", "spain": "ES",
    "sweden": "SE", "switzerland": "CH", "taiwan": "TW", "thailand": "TH",
    "tunisia": "TN", "türkiye": "TR", "turkey": "TR", "ukraine": "UA",
    "united arab emirates": "AE", "united kingdom": "GB", "united states": "US",
    "uzbekistan": "UZ", "vietnam": "VN",
}


def country_code(name: str) -> str:
    """Resolve a country name to its ISO 2-letter code."""
    return COUNTRY_CODES.get(name.strip().lower(), name.strip()[:2].upper())
