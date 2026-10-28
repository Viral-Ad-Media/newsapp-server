"""Match complete country tokens; a country edition never includes the global desk."""
import re
from django.db.models import Q

COUNTRY_ALIASES = {
    "ng": ["nigeria"], "us": ["usa", "united states", "united states of america"],
    "gb": ["uk", "united kingdom"], "ca": ["canada"], "au": ["australia"],
    "in": ["india"], "gh": ["ghana"], "ke": ["kenya"], "za": ["south africa"],
    "de": ["germany"], "fr": ["france"], "jp": ["japan"], "cn": ["china"],
}

COUNTRY_NAMES = {'ng': 'Nigeria', 'us': 'United States', 'gb': 'United Kingdom', 'ca': 'Canada', 'au': 'Australia', 'nz': 'New Zealand', 'gh': 'Ghana', 'ke': 'Kenya', 'za': 'South Africa', 'eg': 'Egypt', 'ma': 'Morocco', 'tz': 'Tanzania', 'ug': 'Uganda', 'et': 'Ethiopia', 'rw': 'Rwanda', 'sn': 'Senegal', 'cm': 'Cameroon', 'ci': 'Côte d’Ivoire', 'de': 'Germany', 'fr': 'France', 'es': 'Spain', 'it': 'Italy', 'pt': 'Portugal', 'nl': 'Netherlands', 'be': 'Belgium', 'ch': 'Switzerland', 'at': 'Austria', 'ie': 'Ireland', 'se': 'Sweden', 'no': 'Norway', 'dk': 'Denmark', 'fi': 'Finland', 'pl': 'Poland', 'gr': 'Greece', 'ua': 'Ukraine', 'tr': 'Türkiye', 'ro': 'Romania', 'cz': 'Czechia', 'in': 'India', 'pk': 'Pakistan', 'bd': 'Bangladesh', 'lk': 'Sri Lanka', 'cn': 'China', 'jp': 'Japan', 'kr': 'South Korea', 'sg': 'Singapore', 'my': 'Malaysia', 'id': 'Indonesia', 'ph': 'Philippines', 'th': 'Thailand', 'vn': 'Vietnam', 'ae': 'United Arab Emirates', 'sa': 'Saudi Arabia', 'qa': 'Qatar', 'il': 'Israel', 'br': 'Brazil', 'mx': 'Mexico', 'ar': 'Argentina', 'cl': 'Chile', 'co': 'Colombia', 'pe': 'Peru'}
for _code, _name in COUNTRY_NAMES.items():
    COUNTRY_ALIASES.setdefault(_code, [])
    if _name.lower() not in COUNTRY_ALIASES[_code]:
        COUNTRY_ALIASES[_code].append(_name.lower())

def country_query(location, field="location"):
    location = str(location or "").strip().lower()
    for code, aliases in COUNTRY_ALIASES.items():
        if location in aliases:
            location = code
            break
    tokens = [location, *COUNTRY_ALIASES.get(location, [])]
    pattern = r"(^|[,;|]\s*)(" + "|".join(re.escape(token) for token in tokens) + r")(\s*[,;|]|$)"
    return Q(**{f"{field}__iregex": pattern})

def filter_country(queryset, location):
    return queryset.filter(country_query(location)) if location else queryset
