"""Recover only unambiguous catalogue matches for old attribution-only records."""
import json
from urllib.parse import urlsplit
from django.db.models import Q
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


# Publisher-advertised feeds. Scope labels remain explicit; these do not claim
# that a specialist feed contains every category or country.
OFFICIAL_FEEDS = {
 'nasa.gov': {'url':'https://www.nasa.gov','feed':'https://www.nasa.gov/feed/','category':'Science','location':'us','scope':'NASA news and science'},
 'cisa.gov': {'url':'https://www.cisa.gov','feed':'https://www.cisa.gov/cybersecurity-advisories/all.xml','category':'Technology','location':'us','scope':'CISA cybersecurity advisories'},
 'energy.gov': {'url':'https://www.energy.gov','feed':'https://www.energy.gov/rss/hgeo/2282977','category':'Environment','location':'us','scope':'DOE hydrocarbon and geothermal energy news'},
 'unesco.org': {'url':'https://www.unesco.org','feed':'https://connect.unevoc.unesco.org/unevoc_news.xml','category':'Education','location':'world','scope':'UNESCO-UNEVOC education and training news'},
 'nairametrics.com': {'url':'https://nairametrics.com','feed':'https://nairametrics.com/feed/','category':'Business','location':'ng','scope':'Nairametrics news'},
}


def publisher_host(url):
    host=(urlsplit(url or '').hostname or '').lower().removeprefix('www.')
    return next((root for root in OFFICIAL_FEEDS if host==root or host.endswith('.'+root)),host)


def effective_source(source):
    seen=set()
    while source and source.feed_owner_id and source.pk not in seen:
        seen.add(source.pk);source=source.feed_owner
    return source


def source_group_ids(ids):
    from .models import NewsSource
    roots=set()
    for source in NewsSource.objects.filter(pk__in=ids).select_related('feed_owner'):
        roots.add(effective_source(source).pk)
    return set(NewsSource.objects.filter(Q(pk__in=roots)|Q(feed_owner_id__in=roots)).values_list('pk',flat=True))


def repair_feed_source(source):
    """Configure old reference-only records, retaining IDs and source histories."""
    from .models import NewsSource,NewsCategory
    if source.feed_owner_id:
        return effective_source(source)
    if (source.feed_url or '').strip():return source
    official=OFFICIAL_FEEDS.get(publisher_host(source.url))
    feed=official['feed'] if official else catalogue_feed(source.name,source.url,source.location)
    if not feed:return None
    owner=NewsSource.objects.filter(feed_url=feed).exclude(pk=source.pk).first()
    if owner:
        source.feed_owner=effective_source(owner)
        source.save(update_fields=['feed_owner'])
        return effective_source(owner)
    source.feed_url=feed
    fields=['feed_url']
    if official:
        source.url=official['url'];source.location=official['location'];fields+=['url','location']
        if not source.default_category_id:
            source.default_category=NewsCategory.objects.filter(name__iexact=official['category']).first()
            fields.append('default_category')
        if not source.description:
            source.description=official['scope'];fields.append('description')
    source.save(update_fields=fields)
    return source
