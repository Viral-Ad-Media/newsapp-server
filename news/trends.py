"""Google's public Trending Now exports: discovery signals, never article evidence."""
import math
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import timedelta
from email.utils import parsedate_to_datetime
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from .models import TrendSnapshot, NewsSource
from .regions import normalize_country
from .safe_fetch import fetch_document
from .utils.rss_ingestor import strip_html_tags

GLOBAL_MARKETS = ('NG','US','GB','IN','ZA','KE','BR','CA','AU','DE','FR','JP','KR','ID','MX','EG')
STOP = set('the and for from with this that news live latest today updates breaking new about after over into says'.split())


def countries():
    values = {normalize_country(value).upper() for value in NewsSource.objects.filter(enabled=True).values_list('location',flat=True)}
    return sorted(set(GLOBAL_MARKETS) | {value for value in values if re.fullmatch('[A-Z]{2}',value)})


def parse_trends(xml, country, now=None):
    now = now or timezone.now()
    if len(xml) > 2*1024*1024 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('Invalid trends XML')
    root = ET.fromstring(xml)
    if root.tag.split('}')[-1] != 'rss': raise ValueError('Not a Trends RSS export')
    topics = []
    for item in root.findall('./channel/item')[:40]:
        fields = {child.tag.split('}')[-1]:child.text or '' for child in item}
        query = strip_html_tags(fields.get('title','')).strip()[:200]
        try:
            started = parsedate_to_datetime(fields.get('pubDate',''))
            if timezone.is_naive(started): started = timezone.make_aware(started)
        except (ValueError, TypeError, OverflowError): continue
        if not query or not now-timedelta(hours=48) <= started <= now+timedelta(minutes=5): continue
        traffic = fields.get('approx_traffic','')[:40]
        number = re.search(r'([\d,.]+)\s*([KM]?)',traffic.upper())
        volume = int(float(number[1].replace(',','')) * {'':1,'K':1000,'M':1000000}[number[2]]) if number else 0
        topics.append({'query':query,'country':country,'traffic':traffic,'volume':volume,'started_at':started.isoformat()})
    return topics


def tick_trends():
    now = timezone.now()
    row = TrendSnapshot.objects.filter(next_attempt__lte=now).order_by('next_attempt','country').first()
    if row is None: return None
    # A short durable lease also protects multiple worker processes during rollout.
    if not TrendSnapshot.objects.filter(pk=row.pk,next_attempt__lte=now).update(next_attempt=now+timedelta(minutes=5)): return None
    try:
        if not re.fullmatch('[A-Z]{2}',row.country): raise ValueError('Unsupported trend country')
        xml = fetch_document(f'https://trends.google.com/trending/rss?geo={row.country}',timeout=5,xml=True,accept='application/rss+xml, application/xml, text/xml',allowed_host='trends.google.com',allow_query=True)
        topics = parse_trends(xml,row.country,now)
        if not topics: raise ValueError('No recent trends returned')
        TrendSnapshot.objects.filter(pk=row.pk).update(topics=topics,fetched_at=now,error='',next_attempt=now+timedelta(minutes=30))
        cache.delete('google-trends-topics-v1')
    except Exception:
        # Preserve last-good data for at most six hours, with no retry storm.
        TrendSnapshot.objects.filter(pk=row.pk).update(error='Google Trends temporarily unavailable or no recent topics; source recency fallback is active.',next_attempt=now+timedelta(minutes=30))
    return row


def initialise_trends():
    TrendSnapshot.objects.bulk_create([TrendSnapshot(country=value) for value in countries()],ignore_conflicts=True)


def available_topics():
    topics = cache.get('google-trends-topics-v1')
    if topics is None:
        cutoff = timezone.now()-timedelta(hours=6)
        topics = [topic for row in TrendSnapshot.objects.filter(fetched_at__gte=cutoff).values_list('topics',flat=True) for topic in row if topic.get('started_at','') >= (timezone.now()-timedelta(hours=48)).isoformat()]
        cache.set('google-trends-topics-v1',topics,60)
    return topics


def tokens(value):
    text = unicodedata.normalize('NFKC',str(value or '')).casefold()
    return tuple(token for token in re.findall(r'[^\W_]+',text,flags=re.UNICODE) if token not in STOP and len(token)>1)


def rank_items(items, source, enabled=True):
    from .seo import words
    topics = available_topics() if enabled else []
    occurrences = Counter(' '.join(tokens(topic['query'])) for topic in topics)
    country = normalize_country(source.location).upper()
    prepared_topics = []
    for topic in topics:
        terms = set(tokens(topic['query']))
        global_topic = occurrences[' '.join(tokens(topic['query']))] > 1
        regional = topic['country'] == country
        if terms and (country == 'WORLD' or regional or global_topic):
            prepared_topics.append((topic,terms,regional))
    ranked=[]
    for item in items:
        item.pop('trend',None)
        headline = set(tokens(item.get('title','')))
        text = set(tokens(item.get('title','')+' '+item.get('description','')[:500]))
        best, match = 0, None
        for topic, terms, regional in prepared_topics:
            # Whole tokens and headline entity overlap avoid substring matches
            # and incidental names buried in unrelated summary text.
            if not terms <= text or len(terms & headline) < min(2,len(terms)): continue
            score = 10*len(terms) + (20 if regional else 10) + min(8,math.log10(max(1,topic.get('volume',0))))
            if score > best: best, match = score, topic
        if match: item['trend'] = {key:match[key] for key in ('query','country','traffic','started_at')}
        evidence = len(words(item.get('source_text') or item.get('content') or '')) >= 400
        date = item.get('published_at')
        stamp = date.timestamp() if hasattr(date,'timestamp') else 0
        ranked.append(((best,evidence,stamp),item))
    return [item for _,item in sorted(ranked,key=lambda pair:pair[0],reverse=True)]


def trends_status():
    rows = list(TrendSnapshot.objects.values('country','fetched_at','error'))
    fresh = [row for row in rows if row['fetched_at'] and row['fetched_at'] >= timezone.now()-timedelta(hours=6)]
    return {'provider':'Google Trends Trending Now RSS','fresh_regions':len(fresh),'configured_regions':len(rows),'last_success':max((r['fetched_at'] for r in fresh),default=None),'mode':'trend matches first; newest valid source reports when no matching trend is available'}
