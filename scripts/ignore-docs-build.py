"""Vercel: exit 0 skips a docs-only push; uncertainty always builds."""
import os
from pathlib import PurePosixPath
import subprocess
import sys

def documentation_only(paths):
    paths = list(paths)
    return bool(paths) and all(path.startswith('docs/') or (len(PurePosixPath(path).parts) == 1 and PurePosixPath(path).suffix.lower() in ('.md','.rst')) for path in paths)

if __name__ == '__main__':
    try:
        previous = os.environ.get('VERCEL_GIT_PREVIOUS_SHA')
        if not previous or not __import__('re').fullmatch('[0-9a-f]{40}',previous): sys.exit(1)
        result = subprocess.run(['git','diff','--name-only','-z',previous,'HEAD','--'],capture_output=True,check=True)
        paths = result.stdout.decode().rstrip('\0').split('\0')
        sys.exit(0 if documentation_only(paths) else 1)
    except (OSError,ValueError,subprocess.SubprocessError): sys.exit(1)
