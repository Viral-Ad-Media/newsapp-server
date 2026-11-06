"""Publisher photographs first; individually allocated, attributed topic photographs second."""
import re
import hashlib
from datetime import timedelta
from html import unescape
from urllib.parse import urlsplit
import requests
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import NewsArticle, TopicPhoto, SourcePhotoCheck
from .source_photos import article_photo, photo_url


def clean(value):
    return unescape(re.sub('<[^>]*>', '', str(value or ''))).strip()


def topic_for(article):
    title = article.title.lower()
    specific = [
        (r'football|soccer', 'association football match'), (r'basketball', 'basketball game'),
        (r'tennis', 'tennis match'), (r'refinery|petroleum|crude oil', 'oil refinery'),
        (r'electric|power grid|kilowatt|grid interconnect|substation|transmission line', 'electricity transmission'),
        (r'cyber|cisa|ransomware|internet|network', 'computer server room'),
        (r'nasa|space|europa|telescope|mars|rocket', 'space rocket launch'),
        (r'hospital|health|maternal|clinic|vaccine', 'hospital medical staff'),
        (r'school|student|education|unesco|university', 'school classroom'),
        (r'bank|stock|equities|finance|exchange|inflation', 'stock exchange trading'),
    ]
    for pattern, topic in specific:
        if re.search(pattern, title): return topic
    categories = ' '.join(article.categories.values_list('name', flat=True)).lower()
    for pattern, topic in [
        ('sport', 'athletics competition'), ('entertainment|culture', 'concert performance'),
        ('business|econom', 'market traders'), ('science', 'scientist laboratory'),
        ('tech', 'computer server room'), ('environment|energy', 'solar power panels'),
        ('health', 'hospital medical staff'), ('education', 'school classroom'),

    ]:
        if re.search(pattern, categories): return topic
    # Do not assign an unrelated default photo to an unrecognised subject.
    return ' '.join(re.findall(r'[a-zA-Z]{4,}', title)[:5])


def curated_photos(topic):
    # Real photographs already reviewed in the project's image-credit library.
    reviewed = {
        'association football match': ('1574629810360-7efbbe195018', 'Football on a pitch'),
        'hospital medical staff': ('1576091160399-112ba8d25d1d', 'Medical professional with stethoscope'),
        'school classroom': ('1503676260728-1c00da094a0b', 'Classroom materials and school books'),
        'solar power panels': ('1509391366360-2e959784a276', 'Solar panels'),
        'oil refinery': ('1596980786765-775174984ec9', 'Industrial oil refinery'),
        'electricity transmission': ('1473341304170-971dccb5ac1e', 'Electricity transmission lines'),
        'computer server room': ('1558494949-ef010cbdcc31', 'Data centre infrastructure'),
        'stock exchange trading': ('1554224155-6726b3ff858f', 'Financial documents and calculator'),
    }
    if topic not in reviewed: return []
    identifier, caption = reviewed[topic]
    original = f'https://images.unsplash.com/photo-{identifier}'
    return [{'key': hashlib.sha256(original.encode()).hexdigest(), 'topic': topic,
             'url': original + '?auto=format&fit=crop&w=1400&q=85',
             'reference_url': original, 'credit': 'Unsplash · Unsplash licence', 'caption': caption}]


def commons_photos(topic, offset=0):
    if not topic: return []
    with requests.Session() as session:
        session.trust_env = False
        response = session.get('https://commons.wikimedia.org/w/api.php', params={
            'action': 'query', 'format': 'json', 'formatversion': 2,
            'generator': 'search', 'gsrnamespace': 6, 'gsrlimit': 12, 'gsroffset': min(offset, 9000),
            'gsrsearch': topic + ' filetype:bitmap',
            'prop': 'imageinfo', 'iiprop': 'url|size|mime|sha1|extmetadata|metadata', 'iiurlwidth': 1200,
        }, headers={'User-Agent': 'AbokiNews/1.0 (editorial photo attribution)'}, timeout=(3, 6), allow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 3000000: return []
        pages = response.json().get('query', {}).get('pages', [])
    results = []
    for page in pages:
        info = (page.get('imageinfo') or [{}])[0]
        meta = info.get('extmetadata') or {}
        value = lambda name: clean(meta.get(name, {}).get('value', ''))
        license_name = value('LicenseShortName')
        if not re.fullmatch(r'(?:CC BY(?:-SA)? [1-4]\.0|CC0(?: 1\.0)?|Public domain)', license_name, re.I): continue
        title = clean(page.get('title', '')).removeprefix('File:')
        description = value('ImageDescription')
        evidence = title + ' ' + description + ' ' + value('Categories')
        if re.search(r'\b(drawing|diagram|screenshot|logo|illustration|rendering|AI.generated|map of|murdered|massacre|corpse|execution)\b', evidence, re.I): continue
        camera = any(m.get('name') in ('Make', 'Model', 'DateTimeOriginal') for m in info.get('metadata', []))
        if not camera and not re.search(r'photograph|photography|photo taken|images from geograph|flickr images', evidence, re.I): continue
        if info.get('mime') not in ('image/jpeg', 'image/png', 'image/webp') or info.get('width', 0) < 600 or info.get('height', 0) < 300: continue
        url = photo_url(info.get('thumburl') or info.get('url'))
        reference = photo_url(info.get('descriptionurl'))
        if urlsplit(url).hostname not in ('upload.wikimedia.org', 'thumb.wikimedia.org') or urlsplit(reference).hostname != 'commons.wikimedia.org': continue
        artist = value('Artist')
        credit = f'{artist} · {license_name} · Wikimedia Commons'
        if not artist or len(credit) > 500 or not info.get('sha1'): continue
        results.append(dict(key=info['sha1'], topic=topic, url=url, reference_url=reference, credit=credit, caption=title[:255]))
    return results


def current_photo(article):
    for value in [str(article.image.url) if article.image else '', article.remote_image_url, article.og_image_url]:
        url = photo_url(value)
        if url: return {'image_url': url, 'image_alt': article.image_alt or article.title, 'image_credit': article.image_credit, 'image_origin': article.image_origin, 'image_reference_url': article.image_reference_url}
    return None


def asset_payload(asset):
    return {'image_url': asset.url, 'image_alt': asset.caption, 'image_credit': asset.credit, 'image_origin': 'topic', 'image_reference_url': asset.reference_url}


def resolve_photo(article, *, fallback=False, source_first=True, fast=False):
    if not fallback:
        current = current_photo(article)
        if current: return current
    check, _ = SourcePhotoCheck.objects.get_or_create(article=article)
    if check.asset_id: return asset_payload(check.asset)
    now = timezone.now()
    # An atomic lease stops cards and article pages from duplicating searches.
    claimed = SourcePhotoCheck.objects.filter(pk=check.pk).filter(Q(next_attempt__lte=now) | Q(next_attempt__isnull=True, attempts=0)).update(next_attempt=now + timedelta(minutes=5), status='searching')
    if not claimed: return None
    reason = 'No eligible topic photograph returned; retry scheduled.'
    try:
        if source_first and not fallback and article.article_url:
            try:
                from .article_writing import source_document
                from .source_feeds import effective_source
                photo = article_photo(source_document(article.article_url, effective_source(article.source).url, **({"timeout": 2, "redirects": 0} if fast else {})), article.article_url)
                if photo['url']:
                    NewsArticle.all_objects.filter(pk=article.pk).update(remote_image_url=photo['url'], image_alt=photo['alt'] or article.title, image_credit=photo['credit'], image_origin='source', image_reference_url=article.article_url)
                    SourcePhotoCheck.objects.filter(pk=check.pk).update(status='source', checked_at=now, next_attempt=None)
                    article.refresh_from_db()
                    return current_photo(article)
            except Exception:
                reason = 'Publisher image unavailable; searching licensed topic photos.'
        topic = topic_for(article)
        available = list(TopicPhoto.objects.filter(topic=topic, sourcephotocheck__isnull=True).values('key','topic','url','reference_url','credit','caption')[:12])
        if not available:
            available = [photo for photo in curated_photos(topic) if not TopicPhoto.objects.filter(key=photo['key']).exists()]
            if not available:
                available = commons_photos(topic, TopicPhoto.objects.filter(topic=topic).count())
            for values in available:
                TopicPhoto.objects.get_or_create(key=values['key'], defaults=values)
        for values in available:
            try:
                with transaction.atomic():
                    locked = SourcePhotoCheck.objects.select_for_update().get(pk=check.pk)
                    if locked.asset_id: return asset_payload(locked.asset)
                    asset, _ = TopicPhoto.objects.get_or_create(key=values['key'], defaults=values)
                    if SourcePhotoCheck.objects.filter(asset=asset).exists(): continue
                    locked.asset = asset; locked.status = 'topic'; locked.checked_at = now; locked.next_attempt = None
                    locked.save()
                    if not fallback:
                        NewsArticle.all_objects.filter(pk=article.pk).update(remote_image_url=asset.url, image_alt=asset.caption, image_credit=asset.credit, image_origin='topic', image_reference_url=asset.reference_url)
                    return asset_payload(asset)
            except IntegrityError:
                continue
    except (requests.RequestException, ValueError, TypeError, KeyError):
        reason = 'Photo provider temporarily unavailable; retry scheduled.'
    SourcePhotoCheck.objects.filter(pk=check.pk).update(status='retry', checked_at=now, attempts=check.attempts + 1, reason=reason, next_attempt=now + timedelta(hours=1))
    return None


def cached_fallback(article):
    """Reserve an available real photograph without external HTTP discovery."""
    topic=topic_for(article)
    for values in curated_photos(topic):
        TopicPhoto.objects.get_or_create(key=values['key'],defaults=values)
    with transaction.atomic():
        check,_=SourcePhotoCheck.objects.get_or_create(article=article)
        # PostgreSQL cannot lock the nullable asset side of an outer join.
        # The check owns this reservation; lock only that row.
        check=SourcePhotoCheck.objects.select_for_update(of=('self',)).select_related('asset').get(pk=check.pk)
        if check.asset_id:return asset_payload(check.asset)
        used=SourcePhotoCheck.objects.exclude(asset_id=None).values_list('asset_id',flat=True)
        assets=TopicPhoto.objects.filter(topic=topic).exclude(pk__in=used).order_by('pk').select_for_update()
        for asset in assets[:12]:
            if SourcePhotoCheck.objects.filter(asset=asset).exists():continue
            check.asset=asset;check.fallback_requested=True;check.status='pending';check.next_attempt=timezone.now();check.hosted=False
            check.save()
            return asset_payload(asset)
    return None


def process_photo(article, fallback=False):
    from .photo_hosting import mirror_photo, hosting_configured, PhotoRetrievalError
    photo=resolve_photo(article,fallback=fallback)
    if not photo:return None
    check,_=SourcePhotoCheck.objects.get_or_create(article=article)
    try:
        hosted=mirror_photo(article,photo)
        if hosted:
            NewsArticle.all_objects.filter(pk=article.pk).update(remote_image_url=hosted,image_origin=photo['image_origin'],image_alt=photo['image_alt'],image_credit=photo['image_credit'],image_reference_url=photo['image_reference_url'])
            if photo['image_origin']=='topic' and check.asset_id:
                TopicPhoto.objects.filter(pk=check.asset_id).update(url=hosted)
            SourcePhotoCheck.objects.filter(pk=check.pk).update(hosted=True,status=photo['image_origin'],next_attempt=None,checked_at=timezone.now(),reason='')
            return photo
        if not hosting_configured():
            SourcePhotoCheck.objects.filter(pk=check.pk).update(status='retry',next_attempt=timezone.now()+timedelta(hours=1),reason='Image CDN credentials are not configured; source photograph retained.')
            return photo
    except Exception as exc:
        # A failed publisher image gets its own distinct topic photo; a failed
        # CDN copy keeps the available public original and retries later.
        if photo['image_origin']=='source' and isinstance(exc,PhotoRetrievalError):
            fallback_photo=cached_fallback(article) or resolve_photo(article,fallback=True,source_first=False)
            if fallback_photo:
                try:
                    hosted=mirror_photo(article,fallback_photo)
                    if hosted:
                        NewsArticle.all_objects.filter(pk=article.pk).update(remote_image_url=hosted,image_origin='topic',image_alt=fallback_photo['image_alt'],image_credit=fallback_photo['image_credit'],image_reference_url=fallback_photo['image_reference_url'])
                        check.refresh_from_db()
                        if check.asset_id:TopicPhoto.objects.filter(pk=check.asset_id).update(url=hosted)
                        SourcePhotoCheck.objects.filter(pk=check.pk).update(hosted=True,status='topic',next_attempt=None,checked_at=timezone.now(),reason='')
                        return fallback_photo
                except Exception:pass
        SourcePhotoCheck.objects.filter(pk=check.pk).update(status='retry',checked_at=timezone.now(),next_attempt=timezone.now()+timedelta(hours=1),reason='Photograph CDN copy is temporarily unavailable; retry scheduled.')
    return photo


def tick_images():
    # One historical article per iteration; keep publishing ahead of backfills.
    requested = SourcePhotoCheck.objects.filter(article__archived=False,status__in=('pending','retry','searching'),next_attempt__lte=timezone.now()).order_by('next_attempt').first()
    if requested:
        article = NewsArticle.objects.select_related('source').filter(pk=requested.article_id).first()
        if article:
            process_photo(article, fallback=requested.fallback_requested)
            return article
    candidates = NewsArticle.objects.select_related('source').filter(Q(photo_check__isnull=True)|Q(photo_check__hosted=False,photo_check__next_attempt__isnull=True)).order_by('-created_at')[:100]
    for article in candidates:
        process_photo(article)
        return article
    return None


class ArticlePhotoView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, pk):
        article = get_object_or_404(NewsArticle.objects.select_related('source'), pk=pk)
        fallback = request.query_params.get('fallback') == '1'
        photo = None if fallback else current_photo(article)
        check = SourcePhotoCheck.objects.filter(article=article).select_related('asset').first()
        if not photo and check and check.asset_id:
            photo = asset_payload(check.asset)
        if not photo:
            photo = cached_fallback(article)
        if photo:
            return Response(photo, headers={'Cache-Control': 'public, max-age=60' if not fallback else 'private, no-store'})
        # Page views only enqueue missing work. External retrieval and image
        # searches run in the daemon, never in an HTTP function.
        if check is None:
            check,_ = SourcePhotoCheck.objects.get_or_create(article=article)
            SourcePhotoCheck.objects.filter(pk=check.pk).update(status='pending',next_attempt=timezone.now(),fallback_requested=fallback)
        elif check.next_attempt is None or (fallback and not check.fallback_requested):
            SourcePhotoCheck.objects.filter(pk=check.pk).update(status='pending',next_attempt=timezone.now(),fallback_requested=fallback)
        return Response({'status':'preparing'}, status=202, headers={'Cache-Control':'private, no-store'})
