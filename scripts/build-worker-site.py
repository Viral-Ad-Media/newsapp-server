"""Build a tiny static Vercel host; the Django HTTP API now runs on Render.

The Vercel CLI separately builds the daemon declared in vercel.json. This script
must never initialize Django, package Python functions, connect to Neon or migrate.
"""
from pathlib import Path


def build(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    output = root / 'worker-site'
    output.mkdir(exist_ok=True)
    (output / 'index.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="robots" content="noindex"><title>AbokiNews worker</title>'
        '<p>The AbokiNews API is hosted at '
        '<a href="https://newsapp-server-8zzq.onrender.com">Render</a>.</p>'
        '<p>This deployment hosts the background worker. This page does not '
        'indicate worker health.</p></html>', encoding='utf-8')
    return output


if __name__ == '__main__':
    build()
