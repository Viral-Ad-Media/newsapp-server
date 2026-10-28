"""Known legacy title cards must not be returned as article photography."""
import re

def is_placeholder_image(value):
    value = str(value or "")
    return bool(re.search(r"placeholder|feature1\.webp|abokinews/starter/story-(?:2|1[1-9]|20)(?:\.[a-z0-9]+)?(?:$|[?#])", value, re.I))
