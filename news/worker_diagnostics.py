"""Log failure causes without logging connection strings or credentials."""
import re


def safe_worker_error(exc):
    cause = exc.__cause__ or exc
    code = getattr(cause, 'pgcode', None) or getattr(cause, 'sqlstate', None)
    text = str(exc).lower()
    if 'does not exist' in text and ('column' in text or 'relation' in text):
        reason = 'Database schema is behind the worker release. Deploy backend migrations before restarting this worker.'
    elif any(word in text for word in ('connection', 'resolve', 'timeout', 'ssl', 'hostname')):
        reason = 'Database connection failed. Check worker DATABASE_URL, database availability and network access.'
    else:
        reason = 'Worker operation failed; inspect the matching admin run and database schema.'
    detail = f' SQLSTATE {code}.' if code and re.fullmatch(r'[A-Z0-9]{5}', str(code)) else ''
    return f'Worker tick failed ({type(exc).__name__}).{detail} {reason}'
