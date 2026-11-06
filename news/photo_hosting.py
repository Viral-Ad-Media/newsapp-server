"""Verify photographs and copy them to the application's existing Cloudinary CDN."""
from io import BytesIO
import time
from urllib.parse import urlsplit, urljoin
import urllib3
from requests.certs import where
from django.conf import settings
from PIL import Image, ImageOps
from .source_photos import photo_url
from .safe_fetch import validate_feed_url, DocumentHTTPError

MAX_BYTES = 5 * 1024 * 1024


class PhotoRetrievalError(ValueError):
    """The original photo cannot be retrieved or decoded."""


def photo_bytes(url, timeout=5):
    deadline=time.monotonic()+timeout
    for redirect in range(3):
        if not photo_url(url): raise ValueError('Invalid photograph URL')
        parsed,host,address=validate_feed_url(url,allow_query=True)
        pool=urllib3.HTTPSConnectionPool(address,port=443,server_hostname=host,assert_hostname=host,cert_reqs='CERT_REQUIRED',ca_certs=where(),maxsize=1)
        response=None
        try:
            response=pool.urlopen('GET',(parsed.path or '/')+('?' + parsed.query if parsed.query else ''),headers={'Host':host,'User-Agent':'AbokiNews/1.0','Accept':'image/jpeg,image/png,image/webp','Accept-Encoding':'identity'},timeout=urllib3.Timeout(connect=timeout,read=timeout),preload_content=False,redirect=False,retries=False)
            if response.status in (301,302,303,307,308) and redirect<2:
                url=urljoin(url,response.headers.get('Location',''))
                continue
            if response.status!=200:raise DocumentHTTPError(response.status)
            mime=response.headers.get('Content-Type','').split(';')[0].lower()
            if mime not in ('image/jpeg','image/png','image/webp'):raise ValueError('Source did not return a photograph')
            chunks=[];size=0
            while True:
                chunk=response.read(32768,decode_content=False)
                if not chunk:break
                size+=len(chunk)
                if size>MAX_BYTES or time.monotonic()>deadline:raise ValueError('Photograph exceeded download limits')
                chunks.append(chunk)
            return normalized_photo(b''.join(chunks))
        finally:
            if response is not None:response.close()
            pool.close()
    raise ValueError('Photograph redirects exhausted')


def normalized_photo(data):
    # Format and pixel validation reject HTML errors, tracking pixels and SVGs.
    with Image.open(BytesIO(data)) as image:
        if image.format not in ('JPEG','PNG','WEBP') or image.width<240 or image.height<135 or image.width*image.height>24_000_000:
            raise ValueError('Source image is not a usable news photograph')
        image.verify()
    with Image.open(BytesIO(data)) as image:
        image=ImageOps.exif_transpose(image).convert('RGB')
        image.thumbnail((1600,1200))
        output=BytesIO();image.save(output,format='WEBP',quality=84)
        return output.getvalue()


def hosting_configured():
    return all(getattr(settings,key,'') for key in ('CLOUDINARY_CLOUD_NAME','CLOUDINARY_API_KEY','CLOUDINARY_API_SECRET'))


def mirror_photo(article, photo):
    import hashlib
    from cloudinary.uploader import upload
    if not hosting_configured():return None
    parsed=urlsplit(photo['image_url'])
    if parsed.hostname == 'res.cloudinary.com' and parsed.path.split('/')[1:2] == [settings.CLOUDINARY_CLOUD_NAME]:return photo['image_url']
    try:
        data=photo_bytes(photo['image_url'])
    except Exception as exc:
        raise PhotoRetrievalError('Publisher photograph could not be retrieved or decoded.') from exc
    key=hashlib.sha256(f"{article.pk}:{photo['image_url']}".encode()).hexdigest()
    result=upload(BytesIO(data),public_id=f'abokinews/reports/{key}',overwrite=False,unique_filename=False,resource_type='image',timeout=10)
    hosted=photo_url(result.get('secure_url'))
    if urlsplit(hosted).hostname!='res.cloudinary.com':raise ValueError('Image CDN did not return a secure photograph URL')
    return hosted
