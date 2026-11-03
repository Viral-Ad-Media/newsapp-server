"""Safe, stable failure reasons; never expose provider responses or remote secrets."""
import socket
from datetime import timedelta
from django.utils import timezone

MESSAGES = {
 'network_timeout':'Source request timed out. Automatic retry scheduled.',
 'network_unavailable':'Source network connection is temporarily unavailable.',
 'publisher_rate_limit':'Publisher rate limit reached (HTTP 429).',
 'publisher_unavailable':'Publisher is temporarily unavailable (HTTP 5xx).',
 'publisher_blocked':'Publisher blocked access (HTTP 401/403). Permitted full-text evidence is needed.',
 'source_missing':'Original source page is missing (HTTP 404/410).',
 'publisher_policy':'Publisher robots policy does not permit retrieval.',
 'insufficient_evidence':'Insufficient accessible source text to support a full article.',
 'invalid_record':'Feed item is missing a valid title, summary, URL or publication date.',
 'source_security':'Source URL or response did not pass secure retrieval checks.',
 'writing_not_configured':'Writing service is not configured. Set OPENAI_API_KEY on the backend and worker.',
 'writing_auth':'Writing API key was rejected or lacks model access. Check credentials and permissions.',
 'writing_balance':'Writing API quota or balance is exhausted. Check the provider account billing.',
 'writing_rate_limit':'Writing API rate limit reached. Automatic retry scheduled.',
 'writing_timeout':'Writing AI request timed out. Automatic retry scheduled.',
 'writing_unavailable':'Writing provider is temporarily unavailable. Automatic retry scheduled.',
 'invalid_draft':'Writing service returned an invalid draft. No article was published.',
 'quality_checks':'Article did not pass editorial/source-support checks. Review the source evidence.',
 'feed_invalid':'Feed is empty, malformed or contains no parseable entries.',
 'unknown_failure':'Article processing failed. Review the source and writing configuration.',
}
TRANSIENT={'network_timeout','network_unavailable','publisher_rate_limit','publisher_unavailable','writing_rate_limit','writing_timeout','writing_unavailable','invalid_draft'}
WRITING={'writing_not_configured','writing_auth','writing_balance','writing_rate_limit','writing_timeout','writing_unavailable'}
DELAYS=(1,5,15,60,180)

def failure_info(exc, stage='source'):
    details=getattr(exc,'details',{}) or {}
    code=details.get('code')
    if code in MESSAGES:
        return {'code':code,'message':MESSAGES[code],'retryable':code in TRANSIENT,'writing':code in WRITING,'retry_after':details.get('retry_after',0)}
    status=getattr(exc,'status_code',None) or getattr(exc,'status',None)
    try: status=int(status) if status is not None else None
    except (TypeError,ValueError): status=None
    body=getattr(exc,'body',{})
    error=body.get('error',body) if isinstance(body,dict) else {}
    provider_code=(error.get('code') or error.get('type')) if isinstance(error,dict) else None
    text=str(exc).lower()
    name=type(exc).__name__.lower()
    if stage=='writing':
        if provider_code in ('insufficient_quota','billing_hard_limit_reached','billing_not_active'):
            code='writing_balance'
        elif status in (401,403,404): code='writing_auth'
        elif status==429: code='writing_rate_limit'
        elif status and status>=500: code='writing_unavailable'
        elif 'timeout' in name or isinstance(exc,TimeoutError): code='writing_timeout'
        elif 'connection' in name or isinstance(exc,ConnectionError): code='writing_unavailable'
        else: code='invalid_draft'
    else:
        if status==429: code='publisher_rate_limit'
        elif status in (401,403): code='publisher_blocked'
        elif status in (404,410): code='source_missing'
        elif status and status>=500: code='publisher_unavailable'
        elif 'timeout' in name or isinstance(exc,TimeoutError): code='network_timeout'
        elif isinstance(exc,(ConnectionError,socket.gaierror,OSError)) or 'connection' in name or 'resolve' in name: code='network_unavailable'
        elif 'openai_api_key' in text or 'not configured' in text: code='writing_not_configured'
        elif 'robots' in text: code='publisher_policy'
        elif 'insufficient' in text or '400' in text and ('source' in text or 'verified' in text): code='insufficient_evidence'
        elif 'missing title' in text or 'publication time' in text or 'invalid article url' in text: code='invalid_record'
        elif 'feed' in text or 'xml' in text: code='feed_invalid'
        elif 'secure' in text or 'https' in text or 'public' in text or 'publisher' in text and 'host' in text: code='source_security'
        elif 'draft' in text or 'seo' in text or 'source-support' in text or hasattr(exc,'details'): code='quality_checks'
        else: code='feed_invalid' if stage == 'feed' else 'unknown_failure'
    # Safe metadata survives the article writer's exception wrapper.
    if code=='unknown_failure' and (exc.__cause__ or exc.__context__):
        return failure_info(exc.__cause__ or exc.__context__,stage)
    retry_after=getattr(exc,'retry_after',0) or (getattr(getattr(exc,'response',None),'headers',{}) or {}).get('retry-after',0) or 0
    try: retry_after=max(0,min(86400,int(retry_after)))
    except (TypeError,ValueError):
        try:
            from email.utils import parsedate_to_datetime
            retry_after=max(0,min(86400,int((parsedate_to_datetime(str(retry_after))-timezone.now()).total_seconds())))
        except (TypeError,ValueError,OverflowError):retry_after=0
    return {'code':code,'message':MESSAGES[code],'retryable':code in TRANSIENT,'writing':code in WRITING,'retry_after':retry_after}

def retry_time(attempts, info):
    seconds=max(DELAYS[min(max(attempts-1,0),len(DELAYS)-1)]*60,info.get('retry_after',0))
    return timezone.now()+timedelta(seconds=seconds)
