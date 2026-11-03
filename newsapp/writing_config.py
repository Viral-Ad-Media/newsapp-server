"""One credential lookup shared by web requests and the publishing worker."""
from decouple import config


def openai_key():
    # Keep the standard name authoritative; accept the existing operator alias.
    return config('OPENAI_API_KEY', default='').strip() or config('OPEN_AI_API', default='').strip()
