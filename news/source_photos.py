"""Use images declared by the exact source article, never random topic stock."""
import ipaddress
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from .images import is_placeholder_image


def photo_url(value, base=''):
    if not str(value or '').strip(): return ''
    value=urljoin(base,unescape(str(value or '').strip()))
    try:
        parsed=urlsplit(value)
        host=(parsed.hostname or '').lower()
        if parsed.scheme!='https' or not host or parsed.username or parsed.password or parsed.port not in (None,443): return ''
        if host=='localhost' or host.endswith(('.localhost','.local','.internal')): return ''
        try:
            if not ipaddress.ip_address(host).is_global:return ''
        except ValueError:pass
        if is_placeholder_image(value) or re.search(r'(?:^|[/_.-])(?:logo|favicon|avatar|sprite|icon|default|blank|spacer|tracking)(?:[/_.-]|$)',parsed.path,re.I):return ''
        if len(value)>2000:return ''
        return value
    except (ValueError,UnicodeError):return ''


class PhotoHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta={};self.images=[];self.scripts=[];self.script=None;self.in_article=0
    def handle_starttag(self,tag,attrs):
        tag=tag.split(':')[-1]
        attrs=dict(attrs)
        if tag=='meta':
            name=(attrs.get('property') or attrs.get('name') or '').lower()
            self.meta.setdefault(name,attrs.get('content',''))
        if tag in ('article','main'):self.in_article+=1
        if tag=='img':
            width=attrs.get('width','');height=attrs.get('height','')
            if width.isdigit() and int(width)<160 or height.isdigit() and int(height)<90:return
            for field in ('src','data-src','data-original'):
                if attrs.get(field):self.images.append((bool(self.in_article),attrs[field],attrs.get('alt','')))
        if tag=='script' and attrs.get('type','').lower()=='application/ld+json':self.script=[]
    def handle_endtag(self,tag):
        if tag in ('article','main'):self.in_article=max(0,self.in_article-1)
        if tag=='script' and self.script is not None:
            self.scripts.append(''.join(self.script));self.script=None
    def handle_data(self,data):
        if self.script is not None:self.script.append(data)


def article_photo(raw,article_url):
    parser=PhotoHTML();parser.feed(str(raw or ''))
    for name in ('og:image:secure_url','og:image','twitter:image','twitter:image:src'):
        url=photo_url(parser.meta.get(name),article_url)
        if url:return {'url':url,'alt':parser.meta.get('og:image:alt','')[:255],'credit':''}
    def structured(value):
        if isinstance(value,list):
            for entry in value:
                yield from structured(entry)
        elif isinstance(value,dict):
            kind=value.get('@type',[]);kind=[kind] if isinstance(kind,str) else kind
            if any(str(t).split('/')[-1] in ('NewsArticle','Article','ReportageNewsArticle','BlogPosting') for t in kind):
                images=value.get('image',[]);images=images if isinstance(images,list) else [images]
                for image in images:
                    data=image if isinstance(image,dict) else {'url':image}
                    url=photo_url(data.get('contentUrl') or data.get('url'),article_url)
                    if url:yield {'url':url,'alt':str(data.get('caption') or '')[:255],'credit':str(data.get('creditText') or data.get('copyrightNotice') or '')[:500]}
            yield from structured(value.get('@graph',[]))
    for script in parser.scripts:
        try:
            result=next(structured(json.loads(script)),None)
            if result:return result
        except (ValueError,TypeError):pass
    # Only an image inside the report body is eligible as a final page fallback.
    for inside,url,alt in parser.images:
        url=photo_url(url,article_url)
        if inside and url:return {'url':url,'alt':alt[:255],'credit':''}
    return {'url':'','alt':'','credit':''}


def feed_photo(raw,article_url):
    parser=PhotoHTML();parser.feed(str(raw or ''))
    for _,url,alt in parser.images:
        url=photo_url(url,article_url)
        if url:return {'url':url,'alt':alt[:255],'credit':''}
    return {'url':'','alt':'','credit':''}


class SourceEvidence(str):
    def __new__(cls,text,photo):
        value=super().__new__(cls,text);value.photo=photo;return value
