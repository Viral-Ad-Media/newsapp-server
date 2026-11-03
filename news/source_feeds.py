"""Recover only unambiguous catalogue matches for old attribution-only records."""
import json
from urllib.parse import urlsplit
from .source_coverage import CATALOGUE_PATH


def catalogue_feed(name, url, location):
    host = (urlsplit(url or '').hostname or '').lower().removeprefix('www.')
    entries = [entry for entry in json.loads(CATALOGUE_PATH.read_text())['sources'] if (urlsplit(entry['url']).hostname or '').lower().removeprefix('www.') == host]
    exact = [entry for entry in entries if entry['name'].casefold() == (name or '').casefold()]
    if len(exact) == 1:
        return exact[0]['feed_url']
    regional = [entry for entry in entries if entry['location'] == location and entry['category'] == 'World']
    if len(regional) == 1:
        return regional[0]['feed_url']
    if len(entries) == 1:
        return entries[0]['feed_url']
    return None


def discover_feed(url):
    """Use only an RSS/Atom link advertised by this publisher, never guessed URLs."""
    from html.parser import HTMLParser
    from urllib.parse import urljoin
    from .safe_fetch import fetch_document, fetch_xml
    from .utils.rss_ingestor import parse_rss_or_atom_xml
    class Links(HTMLParser):
        candidates = None
        def __init__(self):
            super().__init__(); self.candidates = []
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == 'link' and 'alternate' in values.get('rel', '').split() and values.get('type') in ('application/rss+xml', 'application/atom+xml') and values.get('href'):
                self.candidates.append(urljoin(url, values['href']))
    parser = Links()
    try:
        parser.feed(fetch_document(url, timeout=4))
        host = (urlsplit(url).hostname or '').removeprefix('www.')
        for candidate in parser.candidates[:2]:
            candidate_host = (urlsplit(candidate).hostname or '').removeprefix('www.')
            if candidate_host != host and not candidate_host.endswith('.' + host):
                continue
            if parse_rss_or_atom_xml(fetch_xml(candidate, timeout=4)):
                return candidate
    except Exception:
        pass
    return None
