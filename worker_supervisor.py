"""Keep the durable worker available and recover a stopped or stalled process.

The supervisor owns no application/database credentials beyond the inherited
worker environment and never starts overlapping children.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def supervise(*, command=None, timeout=None, retry_delay=5, check_interval=15):
    timeout = timeout if timeout is not None else int(os.environ.get('NEWS_WORKER_STALL_SECONDS', '900'))
    if timeout < 120:
        raise ValueError('NEWS_WORKER_STALL_SECONDS must be at least 120 seconds.')
    command = command or [sys.executable, 'manage.py', 'run_news_worker']
    stopped = False
    child = None

    def stop(*_):
        nonlocal stopped
        stopped = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with tempfile.TemporaryDirectory(prefix='news-worker-') as directory:
            progress = Path(directory) / 'progress'
            while not stopped:
                progress.touch()
                child = subprocess.Popen(command, env={**os.environ, 'NEWS_WORKER_PROGRESS_FILE': str(progress)})
                stalled = False
                while child.poll() is None and not stopped:
                    age = time.time() - progress.stat().st_mtime
                    if age > timeout:
                        print('Worker stalled; restarting after the active transaction is released.', flush=True)
                        stalled = True
                        break
                    time.sleep(check_interval)
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10 if stalled else 60)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
                if not stopped:
                    print('Worker process exited; retrying automatically.', flush=True)
                    deadline = time.monotonic() + retry_delay
                    while not stopped and time.monotonic() < deadline:
                        time.sleep(min(check_interval, max(0, deadline-time.monotonic())))
    finally:
        if child and child.poll() is None:
            child.kill(); child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    supervise()
