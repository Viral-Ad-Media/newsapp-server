"""Shared, short-lived public response cache, invalidated in the write transaction."""
import hashlib
import json
import math
from datetime import timedelta
from functools import wraps
from django.db.models import Count, Max
from django.utils import timezone
from rest_framework.response import Response
from .models import PublicContentRevision, PublicResponseCache

TTL = 15


def invalidate_public(*, using='default', **kwargs):
    # Append inside the content transaction: rollback-safe, without taking a
    # shared singleton lock that could deadlock worker and editorial writes.
    PublicContentRevision.objects.using(using).create()


def current_generation():
    state = PublicContentRevision.objects.aggregate(total=Count('pk'), last=Max('pk'))
    # Count catches late commits of an earlier allocated sequence ID as well as
    # the latest ID. MAX alone would miss that concurrent-write case.
    raw = f"{state['total']}:{state['last']}".encode()
    return int(hashlib.sha256(raw).hexdigest()[:15], 16)


def _headers(response, remaining=0):
    if remaining:
        response['Cache-Control'] = f'public, max-age=0, s-maxage={remaining}, must-revalidate'
        response['Vercel-CDN-Cache-Control'] = f'public, s-maxage={remaining}'
    else:
        response['Cache-Control'] = 'private, no-store'
    return response


def cached_public(method):
    @wraps(method)
    def wrapped(view, request, *args, **kwargs):
        # Never share member feeds, sessions, staff responses, or personalised data.
        if (request.method != 'GET' or request.user.is_authenticated
                or request.headers.get('Authorization') or request.COOKIES
                or view.__class__.__name__ not in ('NewsList','LopsidedNewsList','EditionView')):
            return _headers(method(view, request, *args, **kwargs))
        generation = current_generation()
        query = sorted((name, tuple(request.query_params.getlist(name))) for name in request.query_params)
        raw = json.dumps([request.get_host(), request.path, query, request.accepted_media_type, generation], sort_keys=True)
        key = hashlib.sha256(raw.encode()).hexdigest()
        now = timezone.now()
        entry = PublicResponseCache.objects.filter(pk=key, expires_at__gt=now).first()
        if entry:
            return _headers(Response(entry.payload), max(1, math.floor((entry.expires_at-now).total_seconds())))
        response = method(view, request, *args, **kwargs)
        if response.status_code == 200 and not response.cookies:
            # A concurrent publish must not insert an old snapshot into a new generation.
            if current_generation() == generation:
                PublicResponseCache.objects.update_or_create(pk=key, defaults={'generation': generation,'payload': response.data,'expires_at': now + timedelta(seconds=TTL)})
                return _headers(response, max(1, math.floor((now + timedelta(seconds=TTL)-timezone.now()).total_seconds())))
        return _headers(response)
    return wrapped


def register_invalidation():
    from django.db.models.signals import post_save, post_delete, m2m_changed
    from .models import NewsArticle, NewsCategory, NewsSource, DevelopingStory, StoryUpdate, ArticleCorrection, HomepagePlacement, HomepageSection
    def changed(sender, using='default', update_fields=None, **kwargs):
        if sender is NewsSource and update_fields and set(update_fields) <= {'enabled','last_attempt','last_success','last_error','retrieved','feed_quality'}:
            return
        invalidate_public(using=using)
    def related_changed(sender, action, using='default', **kwargs):
        if action in ('post_add','post_remove','post_clear'):
            invalidate_public(using=using)
    for model in (NewsArticle, NewsCategory, NewsSource, DevelopingStory, StoryUpdate, ArticleCorrection, HomepagePlacement, HomepageSection):
        post_save.connect(changed, sender=model, weak=False, dispatch_uid=f'public-cache-save-{model.__name__}')
        post_delete.connect(changed, sender=model, weak=False, dispatch_uid=f'public-cache-delete-{model.__name__}')
    for through in (NewsArticle.categories.through, NewsArticle.tags.through):
        m2m_changed.connect(related_changed, sender=through, weak=False, dispatch_uid=f'public-cache-m2m-{through.__name__}')
